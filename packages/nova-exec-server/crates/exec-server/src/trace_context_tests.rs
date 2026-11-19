use super::*;

#[test]
fn trace_headers_are_empty_without_an_active_w3c_span() {
    assert!(current_trace_context_headers().is_empty());
    assert!(current_rendezvous_headers().is_empty());
}
