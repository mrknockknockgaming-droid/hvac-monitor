// Host test for include/send_queue.h:  g++ -std=c++17 -I../../include send_queue_test.cpp && ./a.out
#include <assert.h>
#include <stdio.h>
#include <string>
#include "send_queue.h"

int main() {
  SendQueue<std::string> q(5, 3);

  // live readings, echoed back in order: nothing to resend
  q.sent("a"); q.sent("b");
  q.echoed("b");                          // "a" came before it on the same connection
  assert(q.unconfirmed() == 0);
  assert(q.requeue() == 0 && q.empty());

  // the link dies quietly: c, d go into a dead socket, e is kept while reconnecting
  q.sent("c"); q.sent("d");
  q.keep("e");
  assert(q.requeue() == 2);               // new connection: c, d go first, then e
  assert(q.size() == 3 && q.front() == "c");
  q.pop(); assert(q.front() == "d");
  q.pop(); assert(q.front() == "e");
  q.pop(); assert(q.empty());

  // an echo that matches nothing (someone else's message) changes nothing
  q.sent("f");
  q.echoed("zzz");
  assert(q.unconfirmed() == 1);
  q.echoed("f");
  assert(q.unconfirmed() == 0);

  // unconfirmed holds the newest 3; the backlog the newest 5, counting what it drops
  for (const char* s : {"1", "2", "3", "4"}) q.sent(s);
  assert(q.unconfirmed() == 3);
  for (const char* s : {"5", "6", "7"}) q.keep(s);
  assert(q.requeue() == 3);               // 2 3 4 5 6 7 -> oldest dropped
  assert(q.size() == 5 && q.front() == "3" && q.dropped() == 1);
  for (const char* s : {"8", "9"}) q.keep(s);
  assert(q.size() == 5 && q.front() == "5" && q.dropped() == 3);
  puts("send_queue ok");
  return 0;
}
