use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;

pub const DEV_SSH_FRAME_VERSION: u32 = 1;
pub const MAX_DEV_SSH_DATA_BYTES: usize = 32 * 1024;
pub const MAX_DEV_SSH_FRAME_BYTES: usize = 40 * 1024;
const MAX_DEV_SSH_TEXT_BYTES: usize = 512;

#[derive(Clone, PartialEq, prost::Message)]
pub struct DevSshFrame {
    #[prost(uint32, tag = "1")]
    pub version: u32,
    #[prost(string, tag = "2")]
    pub stream_id: String,
    #[prost(oneof = "dev_ssh_frame::Body", tags = "3, 4, 5, 6")]
    pub body: Option<dev_ssh_frame::Body>,
}

pub mod dev_ssh_frame {
    #[derive(Clone, PartialEq, prost::Oneof)]
    pub enum Body {
        #[prost(message, tag = "3")]
        Open(super::Open),
        #[prost(message, tag = "4")]
        OpenResult(super::OpenResult),
        #[prost(message, tag = "5")]
        Data(super::Data),
        #[prost(message, tag = "6")]
        Close(super::Close),
    }
}

#[derive(Clone, PartialEq, prost::Message)]
pub struct Open {}

#[derive(Clone, PartialEq, prost::Message)]
pub struct OpenResult {
    #[prost(bool, tag = "1")]
    pub ok: bool,
    #[prost(string, tag = "2")]
    pub error: String,
}

#[derive(Clone, PartialEq, prost::Message)]
pub struct Data {
    #[prost(bytes = "vec", tag = "1")]
    pub payload: Vec<u8>,
}

#[derive(Clone, PartialEq, prost::Message)]
pub struct Close {
    #[prost(string, tag = "1")]
    pub reason: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DevSshBodyKind {
    Open,
    OpenResult,
    Data,
    Close,
}

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct DevSshFrameError(pub String);

impl DevSshFrame {
    pub fn open(stream_id: String) -> Self {
        Self::new(stream_id, dev_ssh_frame::Body::Open(Open {}))
    }

    pub fn open_result(stream_id: String, error: Option<String>) -> Self {
        Self::new(
            stream_id,
            dev_ssh_frame::Body::OpenResult(OpenResult {
                ok: error.is_none(),
                error: error.unwrap_or_default(),
            }),
        )
    }

    pub fn data(stream_id: String, payload: Vec<u8>) -> Result<Self, DevSshFrameError> {
        let frame = Self::new(stream_id, dev_ssh_frame::Body::Data(Data { payload }));
        frame.validate()?;
        Ok(frame)
    }

    pub fn close(stream_id: String, reason: String) -> Self {
        Self::new(stream_id, dev_ssh_frame::Body::Close(Close { reason }))
    }

    fn new(stream_id: String, body: dev_ssh_frame::Body) -> Self {
        Self {
            version: DEV_SSH_FRAME_VERSION,
            stream_id,
            body: Some(body),
        }
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, DevSshFrameError> {
        if bytes.len() > MAX_DEV_SSH_FRAME_BYTES {
            return Err(DevSshFrameError("dev-ssh frame exceeds limit".to_string()));
        }
        let frame = <Self as prost::Message>::decode(bytes)
            .map_err(|error| DevSshFrameError(format!("invalid dev-ssh frame: {error}")))?;
        frame.validate()?;
        Ok(frame)
    }

    pub fn encode(&self) -> Result<Vec<u8>, DevSshFrameError> {
        self.validate()?;
        let bytes = <Self as prost::Message>::encode_to_vec(self);
        if bytes.len() > MAX_DEV_SSH_FRAME_BYTES {
            return Err(DevSshFrameError("dev-ssh frame exceeds limit".to_string()));
        }
        Ok(bytes)
    }

    pub fn validate(&self) -> Result<DevSshBodyKind, DevSshFrameError> {
        if self.version != DEV_SSH_FRAME_VERSION {
            return Err(DevSshFrameError("unsupported dev-ssh frame version".into()));
        }
        if self.stream_id.is_empty()
            || self.stream_id.len() > 64
            || self.stream_id.chars().any(char::is_control)
        {
            return Err(DevSshFrameError("invalid dev-ssh stream id".into()));
        }
        match self.body.as_ref() {
            Some(dev_ssh_frame::Body::Open(_)) => Ok(DevSshBodyKind::Open),
            Some(dev_ssh_frame::Body::OpenResult(result)) => {
                validate_text(&result.error)?;
                if !result.ok && result.error.is_empty() {
                    return Err(DevSshFrameError("failed open requires an error".into()));
                }
                Ok(DevSshBodyKind::OpenResult)
            }
            Some(dev_ssh_frame::Body::Data(data)) => {
                if data.payload.is_empty() || data.payload.len() > MAX_DEV_SSH_DATA_BYTES {
                    return Err(DevSshFrameError("invalid dev-ssh data length".into()));
                }
                Ok(DevSshBodyKind::Data)
            }
            Some(dev_ssh_frame::Body::Close(close)) => {
                validate_text(&close.reason)?;
                Ok(DevSshBodyKind::Close)
            }
            None => Err(DevSshFrameError("dev-ssh frame has no body".into())),
        }
    }
}

fn validate_text(value: &str) -> Result<(), DevSshFrameError> {
    if value.len() > MAX_DEV_SSH_TEXT_BYTES || value.chars().any(char::is_control) {
        return Err(DevSshFrameError("invalid dev-ssh diagnostic text".into()));
    }
    Ok(())
}

pub(crate) fn web_socket_config() -> WebSocketConfig {
    let mut config = WebSocketConfig::default();
    config.max_message_size = Some(MAX_DEV_SSH_FRAME_BYTES);
    config.max_frame_size = Some(MAX_DEV_SSH_FRAME_BYTES);
    config
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn data_round_trip_and_limits() {
        let frame = DevSshFrame::data("stream-a".into(), b"ciphertext".to_vec()).expect("frame");
        let decoded = DevSshFrame::decode(&frame.encode().expect("encode")).expect("decode");
        assert_eq!(decoded.stream_id, "stream-a");
        assert!(DevSshFrame::data("stream-a".into(), Vec::new()).is_err());
        assert!(DevSshFrame::data("stream-a".into(), vec![0; MAX_DEV_SSH_DATA_BYTES + 1]).is_err());
    }
}
