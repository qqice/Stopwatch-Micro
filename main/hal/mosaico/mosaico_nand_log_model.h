#pragma once
#include "mosaico_nand_store_model.h"
#include <limits>

// Dedicated logical pages in the SAME Dhara map. Never changes trace pages 0..7.
namespace MosaicoNandStore::LogModel {
using Page=Model::Page;
constexpr uint32_t IndexBase=16,DataBase=64,DataPages=8192,MaxPages=8191,RequiredPages=8256;
constexpr size_t HeaderSize=60,RecordSize=64,RecordsPerPage=31;
constexpr uint32_t PageMagic=0x31474c4d,IndexMagic=0x31494c4d;
enum class Code {Ok,Empty,NotFormatted,Corrupt,Io,Overflow,Verify};
struct State {uint64_t latestSeq=0,oldestSeq=0;int indexCopy=-1;bool adopted=false;};
using Model::get;using Model::put;using Model::crc;using Model::blank;
constexpr uint32_t address(uint64_t seq){return DataBase+uint32_t((seq-1)%DataPages);}
constexpr uint64_t oldest(uint64_t seq){return seq>MaxPages?seq-MaxPages+1:1;}
constexpr bool zeros(const Page& p,size_t from,size_t to){for(size_t i=from;i<to;++i)if(p[i])return false;return true;}
// Header CRC identifies a torn journal-owned spare without accepting its records.
constexpr bool validHeader(const Page& p){
 const auto count=get(p,20,2);
 return get(p,0,4)==PageMagic&&get(p,4,2)==1&&get(p,6,2)==HeaderSize&&get(p,8,8)>0&&count>0&&count<=RecordsPerPage&&zeros(p,22,24)&&get(p,24,4)==crc(p,24)&&zeros(p,28,HeaderSize);
}
constexpr bool validPage(const Page& p){
 const auto count=get(p,20,2);
 return validHeader(p)&&zeros(p,HeaderSize+count*RecordSize,2044)&&get(p,2044,4)==crc(p,2044);
}
constexpr bool finalize(Page& p,uint64_t seq){
 const auto boot=get(p,16,4),count=get(p,20,2);if(!seq||!count||count>RecordsPerPage)return false;
 for(size_t i=0;i<HeaderSize;++i)p[i]=0;
 put(p,0,PageMagic,4);put(p,4,1,2);put(p,6,HeaderSize,2);put(p,8,seq,8);put(p,16,boot,4);put(p,20,count,2);put(p,24,crc(p,24),4);
 for(size_t i=HeaderSize+count*RecordSize;i<2044;++i)p[i]=0;
 put(p,2044,crc(p,2044),4);return true;
}
constexpr void index(Page& p,uint64_t latest){p={};put(p,0,IndexMagic,4);put(p,4,1,2);put(p,6,24,2);put(p,8,latest,8);put(p,16,oldest(latest),8);put(p,2044,crc(p,2044),4);}
constexpr bool validIndex(const Page& p){const auto seq=get(p,8,8);return get(p,0,4)==IndexMagic&&get(p,4,2)==1&&get(p,6,2)==24&&seq&&get(p,16,8)==oldest(seq)&&zeros(p,24,2044)&&get(p,2044,4)==crc(p,2044);}
template<class IO> constexpr Code publish(IO& io,Model::Scratch& s,State& state,uint64_t seq){
 const int copy=state.indexCopy==0?1:0;index(s.page,seq);
 if(!io.write(IndexBase+copy,s.page)||!io.sync()||!io.read(IndexBase+copy,s.verify))return Code::Io;
 if(s.page!=s.verify)return Code::Verify;
 state.latestSeq=seq;state.oldestSeq=oldest(seq);state.indexCopy=copy;return Code::Ok;
}
template<class IO> constexpr Code load(IO& io,Model::Scratch& s,State& out){
 State state;unsigned markers=0;bool unknown=false;
 for(unsigned i=0;i<2;++i){if(!io.read(i,s.page))return Code::Io;if(Model::validMarker(s.page))++markers;else if(!blank(s.page))unknown=true;}
 if(!markers)return unknown?Code::Corrupt:Code::NotFormatted;
 uint64_t seqs[2]{};bool damaged=false;
 for(unsigned i=0;i<2;++i){
  if(!io.read(IndexBase+i,s.page))return Code::Io;
  if(validIndex(s.page))seqs[i]=get(s.page,8,8);
  else if(!blank(s.page)){if(get(s.page,0,4)!=IndexMagic||get(s.page,4,2)!=1)return Code::Corrupt;damaged=true;}
 }
 if(!seqs[0]&&!seqs[1]&&damaged)return Code::Corrupt;
 if(seqs[0]&&seqs[1]&&(seqs[0]==seqs[1]||(seqs[0]>seqs[1]?seqs[0]-seqs[1]:seqs[1]-seqs[0])!=1))return Code::Corrupt;
 if(seqs[0]||seqs[1]){state.indexCopy=seqs[1]>seqs[0]?1:0;state.latestSeq=seqs[state.indexCopy];state.oldestSeq=oldest(state.latestSeq);}
 // Validate the authoritative tail; remaining pages are checked on exact reads.
 if(state.latestSeq){
  if(!io.read(address(state.latestSeq),s.page))return Code::Io;
  if(!validPage(s.page)||get(s.page,8,8)!=state.latestSeq)return Code::Corrupt;
 }
 // Recovery is read-only. Only a single complete expected successor is adopted.
 if(state.latestSeq!=UINT64_MAX){
  const auto next=state.latestSeq+1;
  if(!io.read(address(next),s.page))return Code::Io;
  if(!blank(s.page)){
   if(!validHeader(s.page))return Code::Corrupt;
   const auto found=get(s.page,8,8);
   if(address(found)!=address(next))return Code::Corrupt;
   if(found==next){if(validPage(s.page)){state.latestSeq=next;state.oldestSeq=oldest(next);state.adopted=true;}}
   else if(!state.latestSeq||found>=state.oldestSeq)return Code::Corrupt;
  }
 }
 out=state;return state.latestSeq?Code::Ok:Code::Empty;
}
template<class IO> constexpr Code read(IO& io,Model::Scratch& s,uint64_t seq,Page& out){
 if(!seq)return Code::Corrupt;
 if(!io.read(address(seq),s.page))return Code::Io;
 if(!validPage(s.page)||get(s.page,8,8)!=seq)return Code::Corrupt;
 out=s.page;return Code::Ok;
}
// Caller supplies a freshly loaded state; never trust a cached caller cursor.
template<class IO> constexpr Code append(IO& io,Model::Scratch& s,Page& input,State& state){
 if(state.latestSeq==UINT64_MAX)return Code::Overflow;
 // Persist RAM-only adoption BEFORE spending the spare page on the next write.
 if(state.adopted){const auto code=publish(io,s,state,state.latestSeq);if(code!=Code::Ok)return code;state.adopted=false;}
 const auto next=state.latestSeq+1;if(!finalize(input,next))return Code::Corrupt;
 if(!io.read(address(next),s.page))return Code::Io;
 if(!blank(s.page)){
  if(!validHeader(s.page))return Code::Corrupt;
  const auto prior=get(s.page,8,8);
  if(address(prior)!=address(next))return Code::Corrupt;
  if(prior==next){if(validPage(s.page))return Code::Corrupt;}
  else if(prior>state.latestSeq||prior>=state.oldestSeq)return Code::Corrupt;
 }
 if(!io.write(address(next),input)||!io.sync()||!io.read(address(next),s.verify))return Code::Io;
 if(s.verify!=input)return Code::Verify;
 return publish(io,s,state,next);
}
}

