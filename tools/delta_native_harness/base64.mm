#import <Foundation/Foundation.h>
#include <cstring>
extern "C" int mbedtls_base64_decode(unsigned char* out,size_t cap,size_t* count,const unsigned char* input,size_t length) {
 @autoreleasepool {
  NSData* source=[NSData dataWithBytes:input length:length];
  NSData* decoded=[[[NSData alloc] initWithBase64EncodedData:source options:0] autorelease];
  if(!decoded||decoded.length>cap)return -1;
  *count=decoded.length;std::memcpy(out,decoded.bytes,*count);return 0;
 }
}
