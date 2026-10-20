#pragma once
#include <cstddef>
#include <cstdint>

// Closed public-metadata-003 complement of the unchanged 59 + 7 snapshot.
// No security/OTP fields, generic address API, parameter writes or unit claims.
namespace mosaico_public_extra {
enum class Selector:uint16_t {
    A9182=0x9182,
    A91B5=0x91B5,
    A91B6=0x91B6,
    A91B7=0x91B7,
    A91B8=0x91B8,
    A91BA=0x91BA,
    A91BC=0x91BC,
    A91BE=0x91BE,
    A91C0=0x91C0,
    A91C2=0x91C2,
    A91C4=0x91C4,
    A91C6=0x91C6,
    A91C8=0x91C8,
    A91CA=0x91CA,
    A91CC=0x91CC,
    A91CE=0x91CE,
    A91D0=0x91D0,
    A91D2=0x91D2,
    A91D4=0x91D4,
    A91D6=0x91D6,
    A91D8=0x91D8,
    A91DA=0x91DA,
    A91DB=0x91DB,
    A91F5=0x91F5,
    A91F7=0x91F7,
    A91F9=0x91F9,
    A9206=0x9206,
    A9208=0x9208,
    A920B=0x920B,
    A920C=0x920C,
    A920D=0x920D,
    A920E=0x920E,
    A9210=0x9210,
    A9212=0x9212,
    A9219=0x9219,
    A921A=0x921A,
    A921C=0x921C,
    A921E=0x921E,
    A921F=0x921F,
    A9232=0x9232,
    A9234=0x9234,
    A9235=0x9235,
    A9237=0x9237,
    A9239=0x9239,
    A923A=0x923A,
    A923C=0x923C,
    A925B=0x925B,
    A9264=0x9264,
    A927D=0x927D,
    A9282=0x9282,
    A9284=0x9284,
    A9286=0x9286,
    A9287=0x9287,
    A9288=0x9288,
    A928A=0x928A,
    A928C=0x928C,
    A928D=0x928D,
    A928E=0x928E,
    A9290=0x9290,
    A9292=0x9292,
    A9293=0x9293,
    A9294=0x9294,
    A9296=0x9296,
    A9298=0x9298,
    A9299=0x9299
};
enum class Type:uint8_t {I1,I2,U1,U2,H1,H2};
struct Field {Selector selector;Type type;uint8_t width;};
inline constexpr Field fields[]={
    {Selector::A9182,Type::I2,2}, // CC Cal Temp
    {Selector::A91B5,Type::I1,1}, // Int Temp Offset
    {Selector::A91B6,Type::I1,1}, // Ext Temp Offset
    {Selector::A91B7,Type::I1,1}, // Pack V Offset
    {Selector::A91B8,Type::I2,2}, // Internal Model Coefficient 1
    {Selector::A91BA,Type::I2,2}, // Internal Model Coefficient 2
    {Selector::A91BC,Type::I2,2}, // Internal Model Coefficient 3
    {Selector::A91BE,Type::I2,2}, // Internal Model Coefficient 4
    {Selector::A91C0,Type::I2,2}, // External Model Coefficient 1
    {Selector::A91C2,Type::I2,2}, // External Model Coefficient 2
    {Selector::A91C4,Type::I2,2}, // External Model Coefficient 3
    {Selector::A91C6,Type::I2,2}, // External Model Coefficient 4
    {Selector::A91C8,Type::I2,2}, // External Model Coefficient 5
    {Selector::A91CA,Type::I2,2}, // External Model Coefficient b 1
    {Selector::A91CC,Type::I2,2}, // External Model Coefficient b 2
    {Selector::A91CE,Type::I2,2}, // External Model Coefficient b 3
    {Selector::A91D0,Type::I2,2}, // External Model Coefficient b 4
    {Selector::A91D2,Type::I2,2}, // RC0
    {Selector::A91D4,Type::I2,2}, // Voltage Comp Coefficient 1
    {Selector::A91D6,Type::I2,2}, // Voltage Comp Coefficient 2
    {Selector::A91D8,Type::I2,2}, // Voltage Comp Coefficient 3
    {Selector::A91DA,Type::U1,1}, // Voltage Comp Input Multiplier
    {Selector::A91DB,Type::I2,2}, // Voltage Comp Output Divisor
    {Selector::A91F5,Type::I2,2}, // Chg Inhibit Temp Low
    {Selector::A91F7,Type::I2,2}, // Chg Inhibit Temp High
    {Selector::A91F9,Type::I2,2}, // Temp Hys
    {Selector::A9206,Type::H2,2}, // Operation Config A
    {Selector::A9208,Type::H2,2}, // Operation Config B
    {Selector::A920B,Type::U1,1}, // SOC Delta
    {Selector::A920C,Type::U1,1}, // Clk Ctl Reg
    {Selector::A920D,Type::H1,1}, // IO Config
    {Selector::A920E,Type::I2,2}, // Init Discharge Set
    {Selector::A9210,Type::I2,2}, // Init Charge Set
    {Selector::A9212,Type::U2,2}, // Device Type
    {Selector::A9219,Type::U1,1}, // Bus Low Time
    {Selector::A921A,Type::I2,2}, // Offset Cal Inhibit Temp Low
    {Selector::A921C,Type::I2,2}, // Offset Cal Inhibit Temp High
    {Selector::A921E,Type::U1,1}, // Sleep Voltage Time
    {Selector::A921F,Type::U1,1}, // Sleep Current Time
    {Selector::A9232,Type::I2,2}, // OT Chg
    {Selector::A9234,Type::U1,1}, // OT Chg Time
    {Selector::A9235,Type::I2,2}, // OT Chg Recovery
    {Selector::A9237,Type::I2,2}, // OT Dsg
    {Selector::A9239,Type::U1,1}, // OT Dsg Time
    {Selector::A923A,Type::I2,2}, // OT Dsg Recovery
    {Selector::A923C,Type::I1,1}, // Initial Standby
    {Selector::A925B,Type::U1,1}, // Learning Low Temp
    {Selector::A9264,Type::I2,2}, // OverLoad Current
    {Selector::A927D,Type::U1,1}, // Cycle Count Percentage
    {Selector::A9282,Type::I2,2}, // Set Voltage Threshold
    {Selector::A9284,Type::I2,2}, // Clear Voltage Threshold
    {Selector::A9286,Type::U1,1}, // Set % RSOC Threshold
    {Selector::A9287,Type::U1,1}, // Clear % RSOC Threshold
    {Selector::A9288,Type::I2,2}, // Set Voltage Threshold
    {Selector::A928A,Type::I2,2}, // Clear Voltage Threshold
    {Selector::A928C,Type::U1,1}, // Set % RSOC Threshold
    {Selector::A928D,Type::U1,1}, // Clear % RSOC Threshold
    {Selector::A928E,Type::I2,2}, // Set Voltage Threshold
    {Selector::A9290,Type::I2,2}, // Clear Voltage Threshold
    {Selector::A9292,Type::U1,1}, // Set % RSOC Threshold
    {Selector::A9293,Type::U1,1}, // Clear % RSOC Threshold
    {Selector::A9294,Type::I2,2}, // Set Voltage Threshold
    {Selector::A9296,Type::I2,2}, // Clear Voltage Threshold
    {Selector::A9298,Type::U1,1}, // Set % RSOC Threshold
    {Selector::A9299,Type::U1,1}, // Clear % RSOC Threshold
};
inline constexpr size_t fieldCount=sizeof(fields)/sizeof(fields[0]);
static_assert(fieldCount==65,"closed public complement");
constexpr bool allowed(Selector selector){
    for(const auto& field:fields)if(field.selector==selector)return true;
    return false;
}
constexpr const char* typeName(Type type){
    return type==Type::I1?"I1":type==Type::I2?"I2":type==Type::U1?"U1":type==Type::U2?"U2":type==Type::H1?"H1":"H2";
}
enum class Error:uint8_t {NotAttempted, None, Transport, Torn};
constexpr const char* errorName(Error error){
    return error==Error::None?"none":error==Error::Transport?"transport_auth":error==Error::Torn?"torn":"not_attempted";
}
struct Values {uint16_t raw[fieldCount]{};uint8_t readable=0;bool complete=false;Error error=Error::NotAttempted;};
// Reader authenticates all 36 bytes with EXACT echo, length and checksum.
// Only one field's two responses live on the stack, not 65 response blocks.
template<class Reader> constexpr bool readStable(Reader read,Values& out){
    out={};
    for(size_t i=0;i<fieldCount;++i){
        uint8_t a[36]{},b[36]{};
        Error error=Error::None;
        if(!read(fields[i].selector,a)||!read(fields[i].selector,b))error=Error::Transport;
        else for(unsigned j=0;j<fields[i].width;++j)if(a[2+j]!=b[2+j])error=Error::Torn;
        if(error!=Error::None){out={};out.readable=static_cast<uint8_t>(i);out.error=error;return false;}
        uint16_t raw=a[2];
        if(fields[i].width==2)raw=static_cast<uint16_t>((raw<<8)|a[3]);
        out.raw[i]=raw;out.readable=static_cast<uint8_t>(i+1);
    }
    out.complete=true;out.error=Error::None;return true;
}
constexpr void publish(bool success,bool closed,const Values& fresh,Values& cache){
    cache={};
    if(success&&closed)cache=fresh;
}
} // namespace mosaico_public_extra
