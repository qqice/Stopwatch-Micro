#pragma once
#include <stddef.h>
#include <hal/hal.h>
#include <stdint.h>
#include <CommonCrypto/CommonDigest.h>
#define PSA_SUCCESS 0
#define PSA_ALG_SHA_256 1
struct psa_hash_operation_t { CC_SHA256_CTX context; bool live; };
#define PSA_HASH_OPERATION_INIT {}
static inline int psa_hash_setup(psa_hash_operation_t* o,int){o->live=true;return CC_SHA256_Init(&o->context)==1?0:-1;}
static inline int psa_hash_update(psa_hash_operation_t* o,const uint8_t* p,size_t n){assert(!GetHAL().held);return o->live&&CC_SHA256_Update(&o->context,p,(CC_LONG)n)==1?0:-1;}
static inline int psa_hash_finish(psa_hash_operation_t* o,uint8_t* p,size_t cap,size_t* n){if(!o->live||cap<32)return -1;o->live=false;*n=32;return CC_SHA256_Final(p,&o->context)==1?0:-1;}
static inline int psa_hash_abort(psa_hash_operation_t* o){o->live=false;return 0;}
static inline int psa_hash_compute(int,const uint8_t* p,size_t n,uint8_t* out,size_t cap,size_t* len){if(cap<32)return -1;*len=32;return CC_SHA256(p,(CC_LONG)n,out)?0:-1;}
