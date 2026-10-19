#pragma once
// Readings on their way to the broker (net.cpp). Plain MQTT publishes can't tell whether a reading
// arrived: on a link that quietly dies, publish() keeps succeeding into a dead socket until the
// keepalive notices 30-60 s later. So the node subscribes to its own telemetry topic and each
// reading stays "unconfirmed" until the broker echoes it back. When the connection is rebuilt,
// readings that never came back go to the front of the backlog and are sent again; the cloud
// ignores an exact repeat of a reading it already has. Templated on the string type so the host
// test can use std::string (tools/host_tests/send_queue_test.cpp).
#include <deque>
#include <stddef.h>

template <typename S>
class SendQueue {
 public:
  explicit SendQueue(size_t maxBacklog, size_t maxUnconfirmed)
      : maxBacklog_(maxBacklog), maxUnconfirmed_(maxUnconfirmed) {}

  // A reading that couldn't be sent now; the oldest is dropped when full.
  void keep(const S& s) {
    if (backlog_.size() >= maxBacklog_) { backlog_.pop_front(); dropped_++; }
    backlog_.push_back(s);
  }

  // A reading handed to the broker (live or from the backlog), not yet echoed back.
  void sent(const S& s) {
    if (unconfirmed_.size() >= maxUnconfirmed_) unconfirmed_.pop_front();   // too old to matter
    unconfirmed_.push_back(s);
  }

  // The broker echoed this reading: it, and anything sent before it, arrived (one connection keeps order).
  void echoed(const S& s) {
    for (size_t i = 0; i < unconfirmed_.size(); i++) {
      if (unconfirmed_[i] == s) {
        unconfirmed_.erase(unconfirmed_.begin(), unconfirmed_.begin() + i + 1);
        return;
      }
    }
  }

  // A new connection: readings sent on the old one that never came back are sent again first.
  // Returns how many.
  size_t requeue() {
    size_t n = unconfirmed_.size();
    while (!unconfirmed_.empty()) {
      backlog_.push_front(unconfirmed_.back());
      unconfirmed_.pop_back();
    }
    while (backlog_.size() > maxBacklog_) { backlog_.pop_front(); dropped_++; }
    return n;
  }

  bool empty() const { return backlog_.empty(); }
  const S& front() const { return backlog_.front(); }
  void pop() { backlog_.pop_front(); }
  size_t size() const { return backlog_.size(); }
  size_t unconfirmed() const { return unconfirmed_.size(); }
  unsigned long dropped() const { return dropped_; }

 private:
  size_t maxBacklog_, maxUnconfirmed_;
  std::deque<S> backlog_, unconfirmed_;
  unsigned long dropped_ = 0;
};
