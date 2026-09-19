# Additional public intermediate CA

`zerossl-ecc-dv-ca2.pem` supplements incomplete chains served by some self-hosted
DERP endpoints. It is NOT a device/server leaf pin or a self-signed trust bypass.
Source (certificate AIA): http://crt.sectigo.com/ZeroSSLECCDVSSLCA2.crt
The DER was converted to PEM without changing certificate bytes. Before inclusion,
its signature chain and the affected server leaf were verified with OpenSSL against
the unchanged ESP-IDF6.1 `esp_crt_bundle/cacrt_all.pem` trust roots (both OK).
Issuer: Sectigo Public Server Authentication Root E46.
Validity: 2025-09-24 through 2035-09-23.
DER SHA256 (independent of PEM line endings):
bec84fce0daa423f3b3eed38ea48066cc5618f5244ad65cd9f8624a813fa4c3d.

The IDF custom bundle trusts this public intermediate in addition to the default
roots. This is an explicit supplementary trust anchor: administrators should
prefer fixing the server fullchain, review CA distrust/expiry during updates,
and remove the supplement if no longer needed. Never download/trust arbitrary
AIA certificates automatically. Unknown private CAs require administrator-provided
trust material or authenticated DERPMap CertName sha256-raw pins.
