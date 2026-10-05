// AES-128 (encryption only) and CCM (RFC 3610) for the LoRa frames: 13-byte nonce, 8-byte tag.
// Portable C++ so the PC tests and the ESP32s run exactly the same code; frames are ~45 bytes,
// so speed doesn't matter. Checked against FIPS-197 and Python's cryptography (AESCCM).
#pragma once
#include <cstddef>
#include <cstdint>

namespace lorafmt {

class Aes128 {
public:
    explicit Aes128(const uint8_t key[16]);
    void encrypt(const uint8_t in[16], uint8_t out[16]) const;

private:
    uint8_t rk_[176];
};

constexpr size_t CCM_NONCE = 13, CCM_TAG = 8;

// out = ciphertext (n bytes) followed by the tag (8 bytes)
void ccm_encrypt(const uint8_t key[16], const uint8_t nonce[CCM_NONCE], const uint8_t* aad, size_t aad_len,
                 const uint8_t* plain, size_t n, uint8_t* out);
// in = ciphertext followed by the tag; false (and out untouched beyond n bytes) if it doesn't authenticate
bool ccm_decrypt(const uint8_t key[16], const uint8_t nonce[CCM_NONCE], const uint8_t* aad, size_t aad_len,
                 const uint8_t* in, size_t n_with_tag, uint8_t* out);

}  // namespace lorafmt
