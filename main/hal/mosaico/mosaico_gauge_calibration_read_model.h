#pragma once
#include <cstddef>
#include <cstdint>

// Read-only Table 3-2 calibration snapshot. F4 is opaque device-order bytes,
// never IEEE floating point, a calibration command, or a writable parameter.
namespace mosaico_calibration {
enum class Selector : uint16_t {
    CCOffset=0x9180, CCGain=0x9184, CCDelta=0x9188, BoardOffset=0x91B4,
    Filter=0x91DD, Deadband=0x91DE, CCDeadband=0x91DF
};
enum class Type : uint8_t { I2, F4, I1, U1 };
struct Field { Selector selector; const char* name; Type type; uint8_t width; };
inline constexpr Field fields[]={
    {Selector::CCOffset,"CCOffset",Type::I2,2},
    {Selector::CCGain,"CCGain",Type::F4,4},
    {Selector::CCDelta,"CCDelta",Type::F4,4},
    {Selector::BoardOffset,"BoardOffset",Type::I1,1},
    {Selector::Filter,"Filter",Type::U1,1},
    {Selector::Deadband,"Deadband",Type::U1,1},
    {Selector::CCDeadband,"CCDeadband",Type::U1,1}
};
inline constexpr size_t fieldCount=sizeof(fields)/sizeof(fields[0]);
constexpr bool allowed(Selector selector){
    for(const auto& field:fields)if(field.selector==selector)return true;
    return false;
}
struct Values { uint8_t raw[fieldCount][4]{}; uint8_t readable=0; };
constexpr uint32_t rawWord(const Values& values,size_t index){
    uint32_t word=0;
    if(index<fieldCount)for(unsigned j=0;j<fields[index].width;++j)word=(word<<8)|values.raw[index][j];
    return word;
}
constexpr int32_t decoded(const Values& values,size_t index){
    const uint32_t word=rawWord(values,index);
    if(index>=fieldCount||fields[index].type==Type::F4)return 0; // F4 has no numeric interpretation.
    if(fields[index].type==Type::I2)return word&0x8000?int32_t(word)-65536:int32_t(word);
    if(fields[index].type==Type::I1)return word&0x80?int32_t(word)-256:int32_t(word);
    return int32_t(word);
}
// Reader must authenticate the complete 36-byte MAC response against the
// exclusively owned, ACKed selector, including length and checksum.
template<class Reader> constexpr bool readStable(Reader read,Values& out){
    out={};Values fresh{};
    for(size_t i=0;i<fieldCount;++i){
        uint8_t first[36]{},second[36]{};
        if(!read(fields[i].selector,first)||!read(fields[i].selector,second))return false;
        for(unsigned j=0;j<fields[i].width;++j){
            if(first[2+j]!=second[2+j])return false;
            fresh.raw[i][j]=first[2+j];
        }
    }
    fresh.readable=fieldCount;out=fresh;return true;
}
constexpr void publish(bool coordinatorSuccess,const Values& fresh,Values& cache){
    cache={};
    if(coordinatorSuccess&&fresh.readable==fieldCount)cache=fresh;
}
} // namespace mosaico_calibration
