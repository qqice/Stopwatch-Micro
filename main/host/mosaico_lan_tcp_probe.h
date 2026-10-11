#pragma once
#include <cstdint>
#include <atomic>
#include <freertos/FreeRTOS.h>
namespace MosaicoLanTcp {
struct Request { uint32_t destination=0; uint16_t port=0; uint32_t seconds=0; };
// All addresses are host byte order. No payload storage or transport fallback.
bool parse(const char* ip, const char* port, const char* seconds, Request& out);
bool destinationAllowed(uint32_t destination, uint32_t source, uint32_t mask);
struct Snapshot {
 uint64_t requestId=0; uint16_t trialId=0; uint8_t stage=0;
 uint32_t source=0,destination=0,seconds=0; uint16_t sourcePort=0,destinationPort=0;
 // error is the operation result; close errors are independent and never erase it.
 int netifIndex=0,error=0,closeResult=0,closeError=0; bool queued=false,active=false,fdOpen=false,finished=false,cleanupDebt=false,bindingSuccess=false,connected=false;
 uint64_t recvBytes=0,readCalls=0,mismatchCount=0,firstMismatchOffset=0;
 uint8_t expected=0,got=0; int64_t startUs=0,endUs=0,lastProgressUs=0;
};
class Probe {
 public:
 bool request(const Request&, uint16_t trial, uint8_t stage);
 void cancel() { _cancel.store(true); }
 Snapshot snapshot() const;
 void service(bool allowed, uint16_t trial, uint8_t stage);
 // active tracks task execution; cleanupDebt reserves unknown FD state after close failure.
 bool busy() const { return _outstanding.load(); }
 bool quiesce(); // Cancel and return true only after confirmed close success (never on cleanup debt).
 private:
 static void worker(void*); void run(); void publish(const Snapshot&);
 mutable portMUX_TYPE _mux=portMUX_INITIALIZER_UNLOCKED;
 Snapshot _cache; char _interface[16]{};
 std::atomic<bool> _cancel{false}, _outstanding{false};
};
}
