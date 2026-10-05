#include "aes_ccm.h"

#include <cstring>

namespace lorafmt {
namespace {

uint8_t SBOX[256];
bool sbox_ready = false;

inline uint8_t rotl8(uint8_t x, int s) { return static_cast<uint8_t>((x << s) | (x >> (8 - s))); }

// The S-box computed from its definition (multiplicative inverse in GF(2^8), then the affine
// transform), so there is no 256-entry table to mistype.
void make_sbox() {
    uint8_t p = 1, q = 1;
    do {
        p = static_cast<uint8_t>(p ^ (p << 1) ^ ((p & 0x80) ? 0x1B : 0));   // p * 3
        q ^= static_cast<uint8_t>(q << 1);                                   // q / 3
        q ^= static_cast<uint8_t>(q << 2);
        q ^= static_cast<uint8_t>(q << 4);
        if (q & 0x80) q ^= 0x09;
        SBOX[p] = static_cast<uint8_t>(q ^ rotl8(q, 1) ^ rotl8(q, 2) ^ rotl8(q, 3) ^ rotl8(q, 4) ^ 0x63);
    } while (p != 1);
    SBOX[0] = 0x63;
    sbox_ready = true;
}

inline uint8_t xtime(uint8_t x) { return static_cast<uint8_t>((x << 1) ^ ((x & 0x80) ? 0x1B : 0)); }

}  // namespace

Aes128::Aes128(const uint8_t key[16]) {
    if (!sbox_ready) make_sbox();
    static const uint8_t RCON[10] = {0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36};
    memcpy(rk_, key, 16);
    for (int i = 4; i < 44; i++) {
        uint8_t t[4];
        memcpy(t, rk_ + 4 * (i - 1), 4);
        if (i % 4 == 0) {
            const uint8_t t0 = t[0];
            t[0] = static_cast<uint8_t>(SBOX[t[1]] ^ RCON[i / 4 - 1]);
            t[1] = SBOX[t[2]];
            t[2] = SBOX[t[3]];
            t[3] = SBOX[t0];
        }
        for (int j = 0; j < 4; j++) rk_[4 * i + j] = rk_[4 * (i - 4) + j] ^ t[j];
    }
}

void Aes128::encrypt(const uint8_t in[16], uint8_t out[16]) const {
    uint8_t s[16];
    for (int i = 0; i < 16; i++) s[i] = in[i] ^ rk_[i];
    for (int round = 1; round <= 10; round++) {
        uint8_t t[16];
        for (int c = 0; c < 4; c++)                     // SubBytes + ShiftRows (byte r + 4c)
            for (int r = 0; r < 4; r++) t[r + 4 * c] = SBOX[s[r + 4 * ((c + r) % 4)]];
        if (round < 10) {
            for (int c = 0; c < 4; c++) {               // MixColumns
                uint8_t* a = t + 4 * c;
                const uint8_t a0 = a[0], a1 = a[1], a2 = a[2], a3 = a[3], all = a0 ^ a1 ^ a2 ^ a3;
                a[0] ^= all ^ xtime(a0 ^ a1);
                a[1] ^= all ^ xtime(a1 ^ a2);
                a[2] ^= all ^ xtime(a2 ^ a3);
                a[3] ^= all ^ xtime(a3 ^ a0);
            }
        }
        for (int i = 0; i < 16; i++) s[i] = t[i] ^ rk_[16 * round + i];
    }
    memcpy(out, s, 16);
}

namespace {
// CCM with L = 2 (15 - 13-byte nonce), M = 8
void ccm_mac(const Aes128& aes, const uint8_t* nonce, const uint8_t* aad, size_t aad_len, const uint8_t* msg, size_t n,
             uint8_t tag[16]) {
    uint8_t b[16];
    b[0] = static_cast<uint8_t>((aad_len ? 0x40 : 0) | (((CCM_TAG - 2) / 2) << 3) | (2 - 1));
    memcpy(b + 1, nonce, CCM_NONCE);
    b[14] = static_cast<uint8_t>(n >> 8);
    b[15] = static_cast<uint8_t>(n);
    aes.encrypt(b, tag);
    auto absorb = [&](const uint8_t* data, size_t len, size_t skip) {
        // `skip` bytes of the first block are already filled (the AAD length prefix)
        size_t pos = 0;
        while (pos < len) {
            const size_t take = (len - pos < 16 - skip) ? len - pos : 16 - skip;
            for (size_t i = 0; i < take; i++) b[skip + i] = data[pos + i];
            for (size_t i = skip + take; i < 16; i++) b[i] = 0;
            for (int i = 0; i < 16; i++) tag[i] ^= b[i];
            aes.encrypt(tag, tag);
            pos += take;
            skip = 0;
        }
    };
    if (aad_len) {
        memset(b, 0, 16);
        b[0] = static_cast<uint8_t>(aad_len >> 8);
        b[1] = static_cast<uint8_t>(aad_len);
        absorb(aad, aad_len, 2);
    }
    if (n) absorb(msg, n, 0);
}

void ccm_ctr(const Aes128& aes, const uint8_t* nonce, uint16_t i, uint8_t out[16]) {
    uint8_t a[16];
    a[0] = 2 - 1;
    memcpy(a + 1, nonce, CCM_NONCE);
    a[14] = static_cast<uint8_t>(i >> 8);
    a[15] = static_cast<uint8_t>(i);
    aes.encrypt(a, out);
}

void ccm_crypt(const Aes128& aes, const uint8_t* nonce, const uint8_t* in, size_t n, uint8_t* out) {
    uint8_t s[16];
    for (size_t pos = 0; pos < n; pos += 16) {
        ccm_ctr(aes, nonce, static_cast<uint16_t>(pos / 16 + 1), s);
        for (size_t i = 0; i < 16 && pos + i < n; i++) out[pos + i] = in[pos + i] ^ s[i];
    }
}
}  // namespace

void ccm_encrypt(const uint8_t key[16], const uint8_t nonce[CCM_NONCE], const uint8_t* aad, size_t aad_len,
                 const uint8_t* plain, size_t n, uint8_t* out) {
    const Aes128 aes(key);
    uint8_t t[16], s0[16];
    ccm_mac(aes, nonce, aad, aad_len, plain, n, t);
    ccm_crypt(aes, nonce, plain, n, out);
    ccm_ctr(aes, nonce, 0, s0);
    for (size_t i = 0; i < CCM_TAG; i++) out[n + i] = t[i] ^ s0[i];
}

bool ccm_decrypt(const uint8_t key[16], const uint8_t nonce[CCM_NONCE], const uint8_t* aad, size_t aad_len,
                 const uint8_t* in, size_t n_with_tag, uint8_t* out) {
    if (n_with_tag < CCM_TAG) return false;
    const size_t n = n_with_tag - CCM_TAG;
    const Aes128 aes(key);
    ccm_crypt(aes, nonce, in, n, out);
    uint8_t t[16], s0[16];
    ccm_mac(aes, nonce, aad, aad_len, out, n, t);
    ccm_ctr(aes, nonce, 0, s0);
    uint8_t diff = 0;                                  // constant-time compare
    for (size_t i = 0; i < CCM_TAG; i++) diff |= static_cast<uint8_t>((t[i] ^ s0[i]) ^ in[n + i]);
    if (diff) {
        memset(out, 0, n);
        return false;
    }
    return true;
}

}  // namespace lorafmt
