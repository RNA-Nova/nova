use std::collections::HashMap;
use std::sync::Arc;
use std::sync::Mutex;

use nova_exec_server_relay_protocol::RelayMessageFrame;
use nova_exec_server_relay_protocol::relay_message_frame;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

const PHYSICAL_QUEUE_CAPACITY: usize = 256;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Role {
    Environment,
    Harness,
}

#[derive(Debug, thiserror::Error)]
pub enum RendezvousError {
    #[error("connection closed")]
    Closed,
    #[error("agent already connected")]
    AgentAlreadyConnected,
    #[error("agent is unavailable")]
    AgentUnavailable,
    #[error("stream already exists")]
    StreamAlreadyExists,
    #[error("unknown stream")]
    UnknownStream,
    #[error("connection does not own this stream")]
    StreamOwnership,
    #[error("invalid Relay frame: {0}")]
    InvalidFrame(String),
}

pub struct PhysicalConnection {
    pub id: String,
    pub environment_id: String,
    pub registration_id: String,
    pub role: Role,
    outgoing: mpsc::Sender<RelayMessageFrame>,
    close: CancellationToken,
}

impl PhysicalConnection {
    pub fn key(&self) -> (String, String) {
        (self.environment_id.clone(), self.registration_id.clone())
    }

    pub fn close(&self) {
        self.close.cancel();
    }

    pub fn is_closed(&self) -> bool {
        self.close.is_cancelled()
    }

    pub fn closed(&self) -> tokio_util::sync::WaitForCancellationFuture<'_> {
        self.close.cancelled()
    }

    /// 正常数据帧使用有界异步队列施加背压；对端消费慢时暂停源端读取，
    /// 不把短暂的生产/消费速度差误判成连接故障。
    async fn send(&self, frame: RelayMessageFrame) -> Result<(), RendezvousError> {
        if self.is_closed() {
            return Err(RendezvousError::Closed);
        }
        self.outgoing
            .send(frame)
            .await
            .map_err(|_| RendezvousError::Closed)
    }

    /// 连接清扫已经不能等待；reset 尽力投递，失败时直接关闭对端。
    fn try_send(&self, frame: RelayMessageFrame) -> Result<(), RendezvousError> {
        if self.is_closed() {
            return Err(RendezvousError::Closed);
        }
        self.outgoing
            .try_send(frame)
            .map_err(|_| RendezvousError::Closed)
    }
}

struct StreamRoute {
    stream_id: String,
    generation: String,
    agent: Arc<PhysicalConnection>,
    harness: Arc<PhysicalConnection>,
}

impl StreamRoute {
    fn peer(
        &self,
        source: &PhysicalConnection,
    ) -> Result<Arc<PhysicalConnection>, RendezvousError> {
        if source.id == self.agent.id {
            Ok(Arc::clone(&self.harness))
        } else if source.id == self.harness.id {
            Ok(Arc::clone(&self.agent))
        } else {
            Err(RendezvousError::StreamOwnership)
        }
    }
}

#[derive(Default)]
struct RouterInner {
    agents: HashMap<(String, String), Arc<PhysicalConnection>>,
    streams: HashMap<String, StreamRoute>,
}

#[derive(Clone, Default)]
pub struct RendezvousRouter {
    inner: Arc<Mutex<RouterInner>>,
}

impl RendezvousRouter {
    pub fn connect(
        &self,
        environment_id: String,
        registration_id: String,
        role: Role,
    ) -> Result<(Arc<PhysicalConnection>, mpsc::Receiver<RelayMessageFrame>), RendezvousError> {
        let (outgoing, receiver) = mpsc::channel(PHYSICAL_QUEUE_CAPACITY);
        let physical = Arc::new(PhysicalConnection {
            id: Uuid::new_v4().simple().to_string(),
            environment_id,
            registration_id,
            role,
            outgoing,
            close: CancellationToken::new(),
        });
        let mut inner = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if role == Role::Environment {
            let key = physical.key();
            if inner
                .agents
                .get(&key)
                .is_some_and(|agent| !agent.is_closed())
            {
                return Err(RendezvousError::AgentAlreadyConnected);
            }
            inner.agents.insert(key, Arc::clone(&physical));
        }
        Ok((physical, receiver))
    }

    pub async fn forward(
        &self,
        source: &Arc<PhysicalConnection>,
        frame: RelayMessageFrame,
    ) -> Result<(), RendezvousError> {
        frame
            .validate()
            .map_err(|error| RendezvousError::InvalidFrame(error.to_string()))?;
        let stream_id = frame.stream_id.clone();
        let is_reset = matches!(frame.body, Some(relay_message_frame::Body::Reset(_)));
        let (peer, route_generation) = {
            let mut inner = self
                .inner
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if !inner.streams.contains_key(&stream_id) {
                if source.role != Role::Harness
                    || !matches!(
                        frame.body,
                        Some(
                            relay_message_frame::Body::Resume(_)
                                | relay_message_frame::Body::Handshake(_)
                        )
                    )
                {
                    return Err(RendezvousError::UnknownStream);
                }
                let agent = inner
                    .agents
                    .get(&source.key())
                    .filter(|agent| !agent.is_closed())
                    .cloned()
                    .ok_or(RendezvousError::AgentUnavailable)?;
                inner.streams.insert(
                    stream_id.clone(),
                    StreamRoute {
                        stream_id: stream_id.clone(),
                        generation: Uuid::new_v4().simple().to_string(),
                        agent,
                        harness: Arc::clone(source),
                    },
                );
            }
            let route = inner.streams.get(&stream_id).expect("route inserted");
            (route.peer(source)?, route.generation.clone())
        };
        if let Err(error) = peer.send(frame).await {
            peer.close();
            self.close_physical(&peer);
            return Err(error);
        }
        if is_reset {
            self.remove_route(&stream_id, &route_generation);
        }
        Ok(())
    }

    pub async fn abort_stream(&self, stream_id: &str) -> bool {
        let route = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .streams
            .remove(stream_id);
        let Some(route) = route else {
            return false;
        };
        let frame = RelayMessageFrame::reset(stream_id.to_string(), "stream_closed".to_string());
        for peer in [route.agent, route.harness] {
            if peer.send(frame.clone()).await.is_err() {
                peer.close();
            }
        }
        true
    }

    pub fn close_physical(&self, physical: &Arc<PhysicalConnection>) {
        physical.close();
        let routes = {
            let mut inner = self
                .inner
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if physical.role == Role::Environment
                && inner
                    .agents
                    .get(&physical.key())
                    .is_some_and(|agent| agent.id == physical.id)
            {
                inner.agents.remove(&physical.key());
            }
            let stream_ids = inner
                .streams
                .iter()
                .filter(|(_, route)| {
                    route.agent.id == physical.id || route.harness.id == physical.id
                })
                .map(|(stream_id, _)| stream_id.clone())
                .collect::<Vec<_>>();
            stream_ids
                .into_iter()
                .filter_map(|stream_id| inner.streams.remove(&stream_id))
                .collect::<Vec<_>>()
        };
        for route in routes {
            let Ok(peer) = route.peer(physical) else {
                continue;
            };
            let reset =
                RelayMessageFrame::reset(route.stream_id.clone(), "peer_closed".to_string());
            if peer.try_send(reset).is_err() {
                peer.close();
            }
        }
    }

    fn remove_route(&self, stream_id: &str, generation: &str) {
        let mut inner = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if inner
            .streams
            .get(stream_id)
            .is_some_and(|route| route.generation == generation)
        {
            inner.streams.remove(stream_id);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(stream_id: &str, payload: Vec<u8>) -> RelayMessageFrame {
        RelayMessageFrame::data(stream_id.to_string(), 0, payload, None)
    }

    #[tokio::test]
    async fn routes_only_between_registered_generation_peers() {
        let router = RendezvousRouter::default();
        let (agent, mut agent_rx) = router
            .connect("env".to_string(), "reg".to_string(), Role::Environment)
            .expect("agent");
        let (harness, mut harness_rx) = router
            .connect("env".to_string(), "reg".to_string(), Role::Harness)
            .expect("harness");
        router
            .forward(&harness, RelayMessageFrame::resume("stream-a".to_string()))
            .await
            .expect("open stream");
        assert_eq!(agent_rx.recv().await.expect("resume").stream_id, "stream-a");
        router
            .forward(&agent, frame("stream-a", b"ciphertext".to_vec()))
            .await
            .expect("data");
        assert_eq!(
            harness_rx
                .recv()
                .await
                .expect("data")
                .into_data()
                .expect("data body")
                .payload,
            b"ciphertext"
        );
        router.close_physical(&harness);
        assert!(agent_rx.recv().await.is_some(), "agent receives reset");
    }

    type Connected = (
        RendezvousRouter,
        Arc<PhysicalConnection>,
        mpsc::Receiver<RelayMessageFrame>,
        Arc<PhysicalConnection>,
        mpsc::Receiver<RelayMessageFrame>,
    );

    async fn connected_stream() -> Connected {
        let router = RendezvousRouter::default();
        let (agent, mut agent_rx) = router
            .connect("env".to_string(), "reg".to_string(), Role::Environment)
            .expect("agent");
        let (harness, harness_rx) = router
            .connect("env".to_string(), "reg".to_string(), Role::Harness)
            .expect("harness");
        router
            .forward(&harness, RelayMessageFrame::resume("stream-a".to_string()))
            .await
            .expect("open stream");
        assert_eq!(agent_rx.recv().await.expect("resume").stream_id, "stream-a");
        (router, agent, agent_rx, harness, harness_rx)
    }

    #[tokio::test]
    async fn foreign_harness_cannot_take_over_an_existing_stream() {
        let (router, _agent, mut agent_rx, harness, _harness_rx) = connected_stream().await;
        let (foreign, _foreign_rx) = router
            .connect("env".to_string(), "reg".to_string(), Role::Harness)
            .expect("foreign harness");
        assert!(matches!(
            router
                .forward(&foreign, RelayMessageFrame::resume("stream-a".to_string()))
                .await,
            Err(RendezvousError::StreamOwnership)
        ));
        router
            .forward(&harness, frame("stream-a", b"original-owner".to_vec()))
            .await
            .expect("original owner retains the stream");
        assert_eq!(
            agent_rx
                .recv()
                .await
                .expect("data")
                .into_data()
                .expect("data body")
                .payload,
            b"original-owner"
        );
    }

    #[tokio::test]
    async fn full_physical_queue_waits_for_drain_without_disconnecting() {
        let (router, agent, mut agent_rx, harness, _harness_rx) = connected_stream().await;
        for _ in 0..PHYSICAL_QUEUE_CAPACITY {
            router
                .forward(&harness, frame("stream-a", b"ciphertext".to_vec()))
                .await
                .expect("within physical budget");
        }
        let mut blocked = tokio::spawn({
            let router = router.clone();
            let harness = Arc::clone(&harness);
            async move {
                router
                    .forward(&harness, frame("stream-a", b"overflow".to_vec()))
                    .await
            }
        });
        assert!(
            tokio::time::timeout(std::time::Duration::from_millis(25), &mut blocked)
                .await
                .is_err(),
            "a full queue must backpressure the producer"
        );
        assert!(!agent.is_closed());

        agent_rx.recv().await.expect("drain one frame");
        tokio::time::timeout(std::time::Duration::from_secs(1), &mut blocked)
            .await
            .expect("draining releases the producer")
            .expect("forward task")
            .expect("forward succeeds");
        assert!(!agent.is_closed());
    }

    #[tokio::test]
    async fn abort_stream_resets_both_peers_and_allows_stream_reuse() {
        let (router, _agent, mut agent_rx, harness, mut harness_rx) = connected_stream().await;
        assert!(router.abort_stream("stream-a").await);
        assert_eq!(
            agent_rx.recv().await.expect("agent reset").stream_id,
            "stream-a"
        );
        assert_eq!(
            harness_rx.recv().await.expect("harness reset").stream_id,
            "stream-a"
        );
        assert!(!router.abort_stream("stream-a").await);
        router
            .forward(&harness, RelayMessageFrame::resume("stream-a".to_string()))
            .await
            .expect("reopen after reset");
        assert_eq!(
            agent_rx.recv().await.expect("new resume").stream_id,
            "stream-a"
        );
    }
}
