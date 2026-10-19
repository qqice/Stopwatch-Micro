// Fixed, secret-free system log schema. No native structs are persisted.
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
namespace MosaicoSystemLog::Model {
constexpr size_t RecordBytes=64,Capacity=128,ChunkBytes=128;
using Bytes=std::array<uint8_t,RecordBytes>;
// args (all numeric):
// Boot: bootId,resetReason,versionHash,ELF SHA256 words[8].
// Gauge/GaugeBoot: validMask,phase|outcome<<8|mode<<16,SOC,RM,FCC,DC,V,
//   signed I low16|signed Iavg high16,RawCC|cycles<<16,op|battery<<16,rawGauging|rawEcho16<<16 (TxAck/EchoRead/DataRead in validMask).
// Network: accepted,failures,historyAccepted,historyFailures,wifiAttempts,budgetClosures.
// Power: locked,wifiRunning,profile,phase,cpuMHz,cycles,offMs low/high,clockError.
// Ota: stage,received,size,progressBasisPoints,revision,currentSlot,targetSlot,busy,pending.
// Heap: internalFree,internalLargest,psramFree,psramLargest,minInternal.
// Warning: severity(1=W/2=E),staticFormatHash,tagHash(0=unavailable),hookDrops.
// Loss: queueDropped,hookDropped,queuedPotentiallyVolatile,writeFailures.
// Outcome: boot reload status,attempted. No reason strings.
// Panic: core,cause,pc,ra,mcause,mtval,otaPhase,otaOffset,resetReason,magicVersion,reserved.
// Tcp: requestId,stage,error,firstError,retryError,wgWaitMs,elapsedMs,wgReadyBefore,wgReadyAfter.
// Temperatures use conservative integer deci-K bounds: ceil(2831.5)..floor(3181.5).
enum class Type:uint16_t { Boot=1,Gauge=2,GaugeBoot=3,Network=4,Power=5,Ota=6,Heap=7,Warning=8,Loss=9,Outcome=10,Panic=11,Tcp=12 };
struct Record {Type type=Type::Boot;uint16_t flags=0;uint64_t monotonicMs=0,epoch=0;std::array<uint32_t,11> args{};};
template<size_t N> constexpr void put(std::array<uint8_t,N>& b,size_t p,uint64_t v,size_t n){for(size_t i=0;i<n;++i)b[p+i]=uint8_t(v>>(8*i));}
template<size_t N> constexpr uint64_t get(const std::array<uint8_t,N>& b,size_t p,size_t n){uint64_t v=0;for(size_t i=0;i<n;++i)v|=uint64_t(b[p+i])<<(8*i);return v;}
constexpr Bytes encode(const Record& r){Bytes b{};put(b,0,uint16_t(r.type),2);put(b,2,r.flags,2);put(b,4,r.monotonicMs,8);put(b,12,r.epoch,8);for(size_t i=0;i<11;++i)put(b,20+i*4,r.args[i],4);return b;}
constexpr Record decode(const Bytes& b){Record r;r.type=Type(get(b,0,2));r.flags=get(b,2,2);r.monotonicMs=get(b,4,8);r.epoch=get(b,12,8);for(size_t i=0;i<11;++i)r.args[i]=get(b,20+i*4,4);return r;}
constexpr uint32_t crc(const uint8_t* b,size_t n){uint32_t c=~0U;for(size_t i=0;i<n;++i){c^=b[i];for(unsigned j=0;j<8;++j)c=(c>>1)^(0xedb88320U&uint32_t(-int32_t(c&1)));}return ~c;}
constexpr uint32_t hash(const char* s){uint32_t h=2166136261U;if(s)for(size_t i=0;s[i]&&i<512;++i)h=(h^uint8_t(s[i]))*16777619U;return h;}
// Drop newest, preserve boot evidence and already-staged records. Single owner
// or externally locked; pop only after durable append acknowledgement.
struct Queue {
 std::array<Bytes,Capacity> data{};size_t head=0,count=0;uint32_t dropped=0;
 constexpr bool push(const Record& r){if(count==Capacity){++dropped;return false;}data[(head+count)%Capacity]=encode(r);++count;return true;}
 constexpr size_t copy(uint8_t* out,size_t maximum)const{const size_t n=count<maximum?count:maximum;for(size_t i=0;i<n;++i)for(size_t j=0;j<RecordBytes;++j)out[i*RecordBytes+j]=data[(head+i)%Capacity][j];return n;}
 constexpr bool consume(size_t n){if(n>count)return false;head=(head+n)%Capacity;count-=n;return true;}
};
// HAL caches Deferred observations; publish only a complete terminal pair.
// Acknowledgements advance only after enqueue succeeds, so full-queue retries
// never duplicate an accepted prefix or permanently discard boot evidence.
struct BootCapture {
 struct Key {uint64_t sampledUs=0;uint8_t phase=0,outcome=0;
  constexpr bool same(const Key& other)const{return sampledUs==other.sampledUs&&phase==other.phase&&outcome==other.outcome;}
 };
 Key beforeAck{},afterAck{};bool beforeSent=false,afterSent=false,outcomeSent=false,complete=false;
 template<class Snapshot,class EmitGauge,class EmitOutcome>
 constexpr bool update(const Snapshot& before,const Snapshot& after,bool attempted,EmitGauge emitGauge,EmitOutcome emitOutcome){
  if(complete)return true;
  if(before.phase!=1||after.phase!=2||after.outcome==0)return false;
  const Key b{before.sampledUs,before.phase,before.outcome},a{after.sampledUs,after.phase,after.outcome};
  if(!beforeSent||!beforeAck.same(b)){if(!emitGauge(before))return false;beforeAck=b;beforeSent=true;}
  if(!afterSent||!afterAck.same(a)){if(!emitGauge(after))return false;afterAck=a;afterSent=true;outcomeSent=false;}
  if(!outcomeSent){if(!emitOutcome(after.outcome,attempted))return false;outcomeSent=true;}
  complete=true;return true;
 }
};
constexpr bool powerGate(bool otaIdle,bool imageValid,bool policy2,bool telemetryValid,bool temperatureValid,uint16_t mv,uint16_t deciK){return otaIdle&&imageValid&&policy2&&telemetryValid&&temperatureValid&&mv>=3300&&deciK>=2832&&deciK<=3181;}
constexpr bool chunkBounds(uint16_t index,size_t capacity){return index<16&&capacity>=ChunkBytes;}
} // namespace


