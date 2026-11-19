use std::collections::HashMap;
use std::sync::Arc;
use std::sync::Mutex;

use tokio::sync::mpsc;
use uuid::Uuid;

use super::DevSshFrame;

const NODE_QUEUE_CAPACITY: usize = 256;
const CLIENT_QUEUE_CAPACITY: usize = 64;
const MAX_STREAMS_PER_NODE: usize = 64;

#[derive(Debug, thiserror::Error)]
pub enum DevSshRouteError {
    #[error("dev-ssh node is offline")]
    Offline,
    #[error("dev-ssh node is already connected")]
    AlreadyConnected,
    #[error("dev-ssh route is overloaded")]
    Overloaded,
    #[error("dev-ssh stream does not exist")]
    UnknownStream,
    #[error("dev-ssh stream ownership mismatch")]
    Ownership,
}

#[derive(Clone)]
pub struct DevSshNodeHandle {
    pub id: String,
    pub environment_id: String,
    pub outgoing: mpsc::Sender<DevSshFrame>,
}

pub struct DevSshClientHandle {
    pub id: String,
    pub stream_id: String,
    pub node: DevSshNodeHandle,
}

#[derive(Clone)]
struct ClientRoute {
    id: String,
    node_id: String,
    outgoing: mpsc::Sender<DevSshFrame>,
}

#[derive(Default)]
struct RouterInner {
    nodes: HashMap<String, DevSshNodeHandle>,
    streams: HashMap<String, ClientRoute>,
}

#[derive(Clone, Default)]
pub struct DevSshRouter {
    inner: Arc<Mutex<RouterInner>>,
}

impl DevSshRouter {
    pub fn node_online(&self, environment_id: &str) -> bool {
        self.inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .nodes
            .contains_key(environment_id)
    }

    pub(crate) fn connect_node(
        &self,
        environment_id: String,
    ) -> Result<(DevSshNodeHandle, mpsc::Receiver<DevSshFrame>), DevSshRouteError> {
        let mut inner = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if inner.nodes.contains_key(&environment_id) {
            return Err(DevSshRouteError::AlreadyConnected);
        }
        let (outgoing, receiver) = mpsc::channel(NODE_QUEUE_CAPACITY);
        let node = DevSshNodeHandle {
            id: Uuid::new_v4().simple().to_string(),
            environment_id: environment_id.clone(),
            outgoing,
        };
        inner.nodes.insert(environment_id, node.clone());
        Ok((node, receiver))
    }

    pub(crate) fn connect_client(
        &self,
        environment_id: String,
    ) -> Result<(DevSshClientHandle, mpsc::Receiver<DevSshFrame>), DevSshRouteError> {
        let mut inner = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let node = inner
            .nodes
            .get(&environment_id)
            .cloned()
            .ok_or(DevSshRouteError::Offline)?;
        if inner
            .streams
            .values()
            .filter(|route| route.node_id == node.id)
            .count()
            >= MAX_STREAMS_PER_NODE
        {
            return Err(DevSshRouteError::Overloaded);
        }
        let id = Uuid::new_v4().simple().to_string();
        let stream_id = Uuid::new_v4().simple().to_string();
        let (outgoing, receiver) = mpsc::channel(CLIENT_QUEUE_CAPACITY);
        inner.streams.insert(
            stream_id.clone(),
            ClientRoute {
                id: id.clone(),
                node_id: node.id.clone(),
                outgoing,
            },
        );
        Ok((
            DevSshClientHandle {
                id,
                stream_id,
                node,
            },
            receiver,
        ))
    }

    pub(crate) fn forward_from_client(
        &self,
        client: &DevSshClientHandle,
        frame: DevSshFrame,
    ) -> Result<(), DevSshRouteError> {
        if frame.stream_id != client.stream_id {
            return Err(DevSshRouteError::Ownership);
        }
        client
            .node
            .outgoing
            .try_send(frame)
            .map_err(|_| DevSshRouteError::Overloaded)
    }

    pub(crate) fn forward_from_node(
        &self,
        node_id: &str,
        frame: DevSshFrame,
    ) -> Result<(), DevSshRouteError> {
        let route = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .streams
            .get(&frame.stream_id)
            .cloned()
            .ok_or(DevSshRouteError::UnknownStream)?;
        if route.node_id != node_id {
            return Err(DevSshRouteError::Ownership);
        }
        route
            .outgoing
            .try_send(frame)
            .map_err(|_| DevSshRouteError::Overloaded)
    }

    pub(crate) fn close_client(&self, client: &DevSshClientHandle) {
        let removed = {
            let mut inner = self
                .inner
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if inner
                .streams
                .get(&client.stream_id)
                .is_some_and(|route| route.id == client.id)
            {
                inner.streams.remove(&client.stream_id)
            } else {
                None
            }
        };
        if removed.is_some() {
            let _ = client.node.outgoing.try_send(DevSshFrame::close(
                client.stream_id.clone(),
                "client_closed".to_string(),
            ));
        }
    }

    pub(crate) fn drop_stream(&self, stream_id: &str) {
        self.inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .streams
            .remove(stream_id);
    }

    pub(crate) fn close_node(&self, node: &DevSshNodeHandle) {
        let clients = {
            let mut inner = self
                .inner
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if inner
                .nodes
                .get(&node.environment_id)
                .is_some_and(|current| current.id == node.id)
            {
                inner.nodes.remove(&node.environment_id);
            }
            let stream_ids = inner
                .streams
                .iter()
                .filter(|(_, route)| route.node_id == node.id)
                .map(|(stream_id, _)| stream_id.clone())
                .collect::<Vec<_>>();
            stream_ids
                .into_iter()
                .filter_map(|stream_id| {
                    inner
                        .streams
                        .remove(&stream_id)
                        .map(|route| (stream_id, route))
                })
                .collect::<Vec<_>>()
        };
        for (stream_id, client) in clients {
            let _ = client
                .outgoing
                .try_send(DevSshFrame::close(stream_id, "node_closed".to_string()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn multiple_clients_have_independent_bounded_routes() {
        let router = DevSshRouter::default();
        let (node, _node_rx) = router.connect_node("machine-a".into()).expect("node");
        let (client_a, mut rx_a) = router.connect_client("machine-a".into()).expect("a");
        let (client_b, _rx_b) = router.connect_client("machine-a".into()).expect("b");
        router
            .forward_from_node(
                &node.id,
                DevSshFrame::open_result(client_a.stream_id.clone(), None),
            )
            .expect("route a");
        assert_eq!(
            rx_a.blocking_recv().expect("open result").stream_id,
            client_a.stream_id
        );
        assert!(matches!(
            router.forward_from_node(
                &node.id,
                DevSshFrame::open_result(client_b.stream_id.clone(), None)
            ),
            Ok(())
        ));
        router.close_client(&client_a);
        assert!(matches!(
            router.forward_from_node(&node.id, DevSshFrame::open_result(client_a.stream_id, None)),
            Err(DevSshRouteError::UnknownStream)
        ));
    }
}
