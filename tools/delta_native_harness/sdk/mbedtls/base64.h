#pragma once
#include <stddef.h>
extern "C" int mbedtls_base64_decode(unsigned char*,size_t,size_t*,const unsigned char*,size_t);
