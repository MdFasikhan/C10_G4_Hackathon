# Certificate Expiry Runbook

## Symptoms
- Certificate expiry warnings
- TLS handshake failures

## Diagnosis
1. Identify affected domains and certificate issuers.
2. Check certificate expiry dates.
3. Verify renewal automation.

## Immediate Mitigation
1. Renew the certificate through the approved process.
2. Deploy the renewed certificate.
3. Validate TLS connectivity.

## Prevention
- Monitor certificate expiry at 30, 14, and 7 days.
- Use automated certificate renewal.
