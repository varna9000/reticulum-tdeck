# Multi-hop Transport Routing Fixes for micropython-reticulum

## Problem

LXMF message delivery fails when the sender and receiver are connected through one or more transport nodes (e.g. `ESP32-cam --TCP--> RaspPi --TCP--> Laptop --LoRa--> T-Deck`). Announces propagate correctly in both directions, but data packets and resource transfers do not reach their destination.

Two root causes were identified:

1. **Outbound data packets are not routed through transport nodes** — packets to destinations behind a transport node are sent as HDR_1 (direct), but the transport node expects HDR_2 with a `transport_id` to route them correctly.

2. **Resource request packets have no retry mechanism** — when a resource transfer is initiated over a link (e.g. LXMF DIRECT delivery with image), the receiver sends a single resource request after accepting the advertisement. Over LoRa, this first request is frequently lost due to radio timing (the receiver transmits immediately after the sender finishes, before the sender's radio is ready to receive). With no retry, the transfer stalls permanently.

## Fix 1: HDR_2 Auto-routing in `packet.py`

When `Packet.pack()` is called, if the destination hash exists in `Transport.path_table` (meaning it was learned from an HDR_2 announce via a transport node), automatically upgrade the packet from HDR_1 to HDR_2 with the appropriate `transport_id`.

### Changes to `packet.py`

In `Packet.pack()`, before building the header, add:

```python
def pack(self):
    self.destination_hash = self.destination.hash

    # Auto-upgrade to HDR_2 if destination is reachable via a transport
    # node (learned from HDR_2 announces stored in Transport.path_table).
    if (self.header_type == const.HDR_1
            and self.transport_id is None
            and self.packet_type == const.PKT_DATA):
        from .transport import Transport
        _tid = Transport.path_table.get(self.destination_hash)
        if _tid is not None:
            self.header_type = const.HDR_2
            self.transport_id = _tid
            self.flags = self._get_packed_flags()

    # ... rest of pack() unchanged
```

Additionally, the HDR_2 branch in `pack()` must handle data encryption for non-announce packets. Without this, `self.ciphertext` remains `None` and packing fails with a `bytes + NoneType` error:

```python
elif self.header_type == const.HDR_2:
    if self.transport_id is not None:
        self.header += self.transport_id
        self.header += self.destination.hash
        if self.packet_type == const.PKT_ANNOUNCE:
            self.ciphertext = self.data
        else:
            # Encrypt data packets (same as HDR_1 path)
            self.ciphertext = self.destination.encrypt(self.data)
            if hasattr(self.destination, 'latest_ratchet_id'):
                self.ratchet_id = self.destination.latest_ratchet_id
    else:
        raise OSError("Header type 2 requires transport ID")
```

### Why this is needed

The reference Reticulum implementation handles transport routing internally in `Transport.outbound()`. In micropython-reticulum, the transport layer is simpler and doesn't perform this upgrade. Without it, a node sending a message to a destination behind a transport node sends an HDR_1 packet. The transport node receives it but has no `transport_id` to match against its routing table, so the packet is not forwarded to the correct next-hop interface.

## Fix 2: Resource Request Retry in `resource.py` and `link.py`

Add a timeout-based retry mechanism for resource requests on the receiver side. If no resource parts arrive within `REQUEST_RETRY_INTERVAL` seconds after sending a request, re-send the request up to `MAX_REQUEST_RETRIES` times.

### Changes to `resource.py`

Add constants:

```python
REQUEST_RETRY_INTERVAL = 10  # seconds between request retries
MAX_REQUEST_RETRIES = 5
```

Add tracking fields in `Resource.accept()` (receiver initialization):

```python
r.last_request_at = 0
r.request_retries = 0
```

Update `request_next()` to record the request time:

```python
def request_next(self):
    # ... existing missing-parts logic ...
    
    self.window_count = 0
    self.last_request_at = time.time()
    self.request_retries += 1
    self.link.send(req_data, const.CTX_RESOURCE_REQ)
```

Add a new method:

```python
def check_request_timeout(self):
    """(Receiver) Retry resource request if no parts arrived within interval."""
    if self.status != TRANSFERRING:
        return
    if self.last_request_at == 0:
        return
    if time.time() - self.last_request_at < REQUEST_RETRY_INTERVAL:
        return
    if self.request_retries >= MAX_REQUEST_RETRIES:
        self.cancel()
        return
    self.request_next()
```

### Changes to `link.py`

Call `check_request_timeout()` from `Link.check_keepalive()` for all incoming resources:

```python
def check_keepalive(self):
    # ... existing pending/active checks ...
    
    if self.status != Link.ACTIVE:
        return

    # Check resource request timeouts (retry if no parts arrived)
    for r in self.incoming_resources:
        r.check_request_timeout()

    # ... existing stale grace check ...
```

### Why this is needed

Over LoRa links through transport nodes, the first resource request is frequently lost. The receiver sends the request immediately after accepting the resource advertisement — at this point the sender's radio (or the transport node's radio) may still be transitioning from TX to RX mode after transmitting the advertisement. LoRa is half-duplex with non-trivial TX/RX turnaround times, and there is no link-layer acknowledgment.

The reference Reticulum has retry logic in its Resource implementation. The micropython-reticulum Resource currently sends the request once with no retry, causing the transfer to stall permanently if the request is lost.

## Test Topology

```
ESP32-cam ──TCP──> RaspPi (transport) ──TCP──> Laptop (transport) ──LoRa──> T-Deck
```

- Announces propagate correctly in both directions (HDR_1 flooding + HDR_2 via transport)
- Opportunistic LXMF messages (single-packet) now delivered via HDR_2 auto-routing
- LXMF DIRECT delivery (Link + Resource transfer) now completes with request retry
