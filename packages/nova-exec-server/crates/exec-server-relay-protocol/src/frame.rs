use nova_exec_server_protocol_core::protocol::W3cTraceContext;

use crate::RELAY_FRAME_VERSION;
use crate::RelayData;
use crate::RelayHandshake;
use crate::RelayMessageFrame;
use crate::RelayReset;
use crate::RelayResume;
use crate::relay_message_frame;

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum RelayFrameBodyKind {
    Data,
    Ack,
    Resume,
    Reset,
    Heartbeat,
    Handshake,
}

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct RelayFrameError(pub String);

impl RelayMessageFrame {
    pub fn data(
        stream_id: String,
        seq: u32,
        payload: Vec<u8>,
        trace: Option<W3cTraceContext>,
    ) -> Self {
        let (traceparent, tracestate) = trace
            .map(|trace| (trace.traceparent, trace.tracestate))
            .unwrap_or_default();
        Self {
            version: RELAY_FRAME_VERSION,
            stream_id,
            traceparent,
            tracestate,
            body: Some(relay_message_frame::Body::Data(RelayData {
                seq,
                segment_index: 0,
                segment_count: 1,
                payload,
            })),
            ..Self::default()
        }
    }

    pub fn resume(stream_id: String) -> Self {
        Self {
            version: RELAY_FRAME_VERSION,
            stream_id,
            body: Some(relay_message_frame::Body::Resume(RelayResume {
                next_seq: 0,
            })),
            ..Self::default()
        }
    }

    pub fn handshake(stream_id: String, payload: Vec<u8>) -> Self {
        Self {
            version: RELAY_FRAME_VERSION,
            stream_id,
            body: Some(relay_message_frame::Body::Handshake(RelayHandshake {
                payload,
            })),
            ..Self::default()
        }
    }

    pub fn reset(stream_id: String, reason: String) -> Self {
        Self {
            version: RELAY_FRAME_VERSION,
            stream_id,
            body: Some(relay_message_frame::Body::Reset(RelayReset { reason })),
            ..Self::default()
        }
    }

    pub fn validate(&self) -> Result<RelayFrameBodyKind, RelayFrameError> {
        if self.version != RELAY_FRAME_VERSION {
            return Err(RelayFrameError(format!(
                "unsupported relay message frame version {}",
                self.version
            )));
        }
        if self.stream_id.trim().is_empty() {
            return Err(RelayFrameError(
                "relay message frame is missing stream_id".to_string(),
            ));
        }
        match self.body.as_ref() {
            Some(relay_message_frame::Body::Data(data)) => {
                if data.segment_index != 0 || data.segment_count != 1 || data.payload.is_empty() {
                    return Err(RelayFrameError(
                        "relay data message frame is missing required fields".to_string(),
                    ));
                }
                Ok(RelayFrameBodyKind::Data)
            }
            Some(relay_message_frame::Body::AckFrame(_)) => Ok(RelayFrameBodyKind::Ack),
            Some(relay_message_frame::Body::Resume(_)) => Ok(RelayFrameBodyKind::Resume),
            Some(relay_message_frame::Body::Reset(reset)) => {
                if reset.reason.is_empty() {
                    return Err(RelayFrameError(
                        "relay reset message frame is missing reason".to_string(),
                    ));
                }
                Ok(RelayFrameBodyKind::Reset)
            }
            Some(relay_message_frame::Body::Heartbeat(_)) => Ok(RelayFrameBodyKind::Heartbeat),
            Some(relay_message_frame::Body::Handshake(handshake)) => {
                if handshake.payload.is_empty() {
                    return Err(RelayFrameError(
                        "relay handshake message frame is missing payload".to_string(),
                    ));
                }
                Ok(RelayFrameBodyKind::Handshake)
            }
            None => Err(RelayFrameError(
                "relay message frame is missing body".to_string(),
            )),
        }
    }

    pub fn into_data(self) -> Result<RelayData, RelayFrameError> {
        if self.validate()? != RelayFrameBodyKind::Data {
            return Err(RelayFrameError(
                "expected relay data message frame".to_string(),
            ));
        }
        match self.body {
            Some(relay_message_frame::Body::Data(data)) => Ok(data),
            _ => Err(RelayFrameError(
                "expected relay data message frame".to_string(),
            )),
        }
    }

    pub fn into_handshake_payload(self) -> Result<Vec<u8>, RelayFrameError> {
        if self.validate()? != RelayFrameBodyKind::Handshake {
            return Err(RelayFrameError(
                "expected relay handshake message frame".to_string(),
            ));
        }
        match self.body {
            Some(relay_message_frame::Body::Handshake(handshake)) => Ok(handshake.payload),
            _ => Err(RelayFrameError(
                "expected relay handshake message frame".to_string(),
            )),
        }
    }

    pub fn into_reset_reason(self) -> Option<String> {
        match self.body {
            Some(relay_message_frame::Body::Reset(reset)) if !reset.reason.is_empty() => {
                Some(reset.reason)
            }
            _ => None,
        }
    }
}
