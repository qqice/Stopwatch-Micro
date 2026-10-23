#pragma once
#include "mosaico_gauge_cold_proof_model.h"
#include "mosaico_gauge_contrast_model.h"
#include <ota/mosaico_ota.h>

// Trusted platform adapter; structural proof decode is never sufficient.
namespace MosaicoColdCrypto {
using Binding=mosaico_gauge_cold_trial::Binding;
using Digest=mosaico_gauge_cold_trial::Digest;
using Record=mosaico_gauge_cold_store::Record;
// Trusted in-memory result only. Never deserialize or accept caller-set trust
// flags as evidence; retain unchanged after authenticate until actual verify.
struct Authenticated {
    Binding binding{};
    MosaicoOta::VerifiedImageDescriptor images[2]{};
    Digest bindingHash{},pairHash{},packetHash{};
    bool sessionVerified=false,actualImagesVerified=false;
};
bool sha256(const uint8_t* data,size_t size,Digest& out);
struct Hasher {bool operator()(const uint8_t* data,size_t size,Digest& out)const{return sha256(data,size,out);}};
// First call must be BEFORE any RF/ADC owner starts. Atomic one-shot state
// safely publishes to concurrent readers; failure is latched without retry.
void prepareBootIdentity();
mosaico_gauge_cold_trial::BootToken bootIdentity(); // RAM only; never regenerates.
// Candidate parsing only, not trust or any authorization. Original buffer must
// stay alive through further view/hash verification.
bool extractBinding(const Record& record,Binding& candidate);
// Caller holds the OTA storage lease; no battery lock. Verifies full source
// receipt, device MAC, both normal/GCT signatures and the cold-session signature.
// Current GCT phase/generation and all live safety gates remain caller duties.
bool authenticate(const Record& record,const std::array<uint8_t,6>& boardMac,
                  const mosaico_gauge_contrast::Raw& durableSource,
                  Authenticated& out,const char*& reason);
// Separately rehashes both FULL actual images and requires SDK VALID. Caller
// owns OTA lease (checked against the current task). Failure clears proof.
// Never treats a package's claims as actual flash evidence.
bool verifyActualImages(Authenticated& proof,const char*& reason);
// GCS1 fragment (<=266B) carries Binding188+DER; pair bytes are the exact prior
// gauge-contrast images decoded proof. This avoids an oversized UART frame.
bool buildFromFragment(const uint8_t* fragment,size_t size,const char* exactPair,
                       Record& out,const char*& reason);
}
