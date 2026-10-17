#pragma once
#include "gauge_discharge_trace_model.h"

namespace GaugeDischargeTrace::StorageCodec {
// Same trace1 key, legacy canonical schema remains unchanged. Old firmware will
// reject this envelope as corrupt: never clear it on rollback; export with >=71.
constexpr size_t HeaderSize=16;
constexpr uint32_t Magic=0x32435447; // GTC2, little endian
struct Packed { Blob bytes{};size_t size=0; }; // Caller must keep large buffers in PSRAM.
enum class Error { None, Length, Format, Crc };
constexpr uint32_t frameCrc(const Packed& p){
    uint32_t c=0xffffffffU;
    for(size_t i=0;i<p.size;++i){
        if(i>=12 && i<16)continue;
        c^=p.bytes[i];for(unsigned j=0;j<8;++j)c=(c>>1)^(0xedb88320U&uint32_t(-int32_t(c&1)));
    }
    return ~c;
}
constexpr uint8_t delta(const Blob& b,size_t i,size_t count){
    return i>=32+RecordSize && i<32+count*RecordSize ? uint8_t(b[i]^b[i-RecordSize]) : b[i];
}
constexpr void pack(const Blob& canonical,Packed& out){
    const size_t count=size_t(get(canonical,8,2));
    size_t input=0,output=HeaderSize;
    bool fits=count<=Capacity;
    while(fits && input<BlobSize){
        const bool zero=delta(canonical,input,count)==0;
        size_t run=1;
        while(run<128 && input+run<BlobSize && (delta(canonical,input+run,count)==0)==zero)++run;
        const size_t need=1+(zero?0:run);
        if(output+need>=BlobSize){fits=false;break;} // Strictly smaller or legacy fallback.
        out.bytes[output++]=uint8_t((zero?128:0)|(run-1));
        if(!zero)for(size_t j=0;j<run;++j)out.bytes[output++]=delta(canonical,input+j,count);
        input+=run;
    }
    if(!fits){out.bytes=canonical;out.size=BlobSize;return;}
    out.size=output;
    put(out.bytes,0,Magic,4);put(out.bytes,4,1,1);put(out.bytes,5,1,1);
    put(out.bytes,6,BlobSize,2);put(out.bytes,8,output-HeaderSize,2);put(out.bytes,10,0,2);
    put(out.bytes,12,frameCrc(out),4);
}
constexpr Error unpack(const Packed& in,Blob& canonical){
    if(in.size>BlobSize || in.size<HeaderSize)return Error::Length;
    if(in.size==BlobSize){
        // Only the original canonical magic is a legacy record, never a padded frame.
        if(get(in.bytes,0,4)!=0x31544447)return Error::Format;
        canonical=in.bytes;
    }else{
        if(get(in.bytes,0,4)!=Magic || in.bytes[4]!=1 || in.bytes[5]!=1 || get(in.bytes,10,2))return Error::Format;
        if(get(in.bytes,6,2)!=BlobSize || get(in.bytes,8,2)!=in.size-HeaderSize)return Error::Length;
        if(get(in.bytes,12,4)!=frameCrc(in))return Error::Crc;
        size_t input=HeaderSize,output=0;
        while(input<in.size){
            const uint8_t token=in.bytes[input++];const size_t run=(token&127)+1;
            if(run>BlobSize-output)return Error::Length;
            if(token&128){for(size_t j=0;j<run;++j)canonical[output++]=0;}
            else{
                if(run>in.size-input)return Error::Length;
                for(size_t j=0;j<run;++j){
                    const uint8_t value=in.bytes[input++];
                    if(!value)return Error::Format; // Nonzero literal runs only.
                    canonical[output++]=value;
                }
            }
        }
        if(output!=BlobSize)return Error::Length;
        const size_t count=size_t(get(canonical,8,2));if(count>Capacity)return Error::Format;
        for(size_t i=32+RecordSize;i<32+count*RecordSize;++i)canonical[i]^=canonical[i-RecordSize];
    }
    if(get(canonical,BlobSize-4,4)!=crc(canonical))return Error::Crc;
    return Error::None;
}
}
