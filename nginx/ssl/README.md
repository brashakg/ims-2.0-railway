# SSL Certificates

This directory should contain your SSL certificates for HTTPS support.

## Required Files

- `cert.pem` - SSL certificate file
- `key.pem` - Private key file

## Development (Self-Signed Certificate)

For development/testing, you can generate a self-signed certificate:

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout key.pem \
  -out cert.pem \
  -subj "/C=IN/ST=State/L=City/O=Organization/CN=localhost"
```

## Production

For production, use a certificate from a trusted Certificate Authority (CA):

### Option 1: Let's Encrypt (Free)

```bash
# Install certbot
sudo apt-get install certbot

# Get certificate
sudo certbot certonly --standalone -d your-domain.com

# Copy certificates to this directory
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem cert.pem
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem key.pem
```

### Option 2: Commercial Certificate

1. Purchase SSL certificate from a CA (Comodo, DigiCert, etc.)
2. Download the certificate files
3. Copy them to this directory:
   - Certificate file → `cert.pem`
   - Private key file → `key.pem`

## File Permissions

Ensure proper file permissions:

```bash
chmod 644 cert.pem
chmod 600 key.pem
```

## Notes

- Never commit actual certificate files to version control
- Keep your private key (`key.pem`) secure
- Renew certificates before they expire (typically every 90 days for Let's Encrypt)
