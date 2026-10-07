#pragma once
#include "session_status_model.h"

namespace mosaico_lock_sessions {
struct Box { int x,y,w,h; };
constexpr Box card(unsigned slot) { return {48+static_cast<int>(slot)*66,392,54,48}; }
struct BatteryRow { int iconX,textX,textY; };
constexpr BatteryRow batteryRow(int textWidth,int textHeight) {
    const int left=(480-(36+12+textWidth))/2;
    return {left,left+48,334+(36-textHeight)/2};
}
// Colour is an explicitly cached observation from the latest bounded window,
// not proof that a sleeping radio still has an active connection.
constexpr uint32_t color(mosaico_sessions_ui::Status status,bool valid,bool fresh,uint32_t now,uint32_t captured) {
    if (!valid || !fresh || now-captured > 60000U) return 0x343A40;
    return status==mosaico_sessions_ui::Status::Thinking ? 0x9868CD :
           status==mosaico_sessions_ui::Status::Complete ? 0xD18C37 : 0x343A40;
}
struct Corner { int x,y,angle; };
constexpr Corner corner(unsigned index) {
    return index==0 ? Corner{9,9,180} : index==1 ? Corner{44,9,270} :
           index==2 ? Corner{44,38,0} : Corner{9,38,90};
}
}
