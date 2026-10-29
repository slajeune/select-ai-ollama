# Prerequisites

- Oracle Database 26ai with a CDB and PDB created and accessible. Listener needs to be properly configure also
- installed sh schema as this is what I'm using for tests see https://github.com/oracle-samples/db-sample-schemas.git
- Ollama installed on a server reachable by the Oracle DB server. I am using qwen2.5-coder:7b
- Nginx installed on the server hosting Ollama or a reverse proxy server.1.
- Configure Self-Signed Certificate and Nginx Proxy:Security & Proxy Layer.
- Generate a self-signed SSL certificate with proper Subject Alternative Names (SAN) so the Oracle Database wallet trusts the host, then route incoming traffic to Ollama's local port.Generate OpenSSL private key and self-signed
- Create certificate

# Create OpenSSL configuration with SAN matching your server hostname/IP

## create self signed certificate

```
sudo mkdir -p /etc/nginx/ssl
cd /etc/nginx/ssl

cat <<EOF > ollama_ssl.cnf
[req]
default_bits       = 2048
prompt             = no
default_md         = sha256
distinguished_name = dn
x509_extensions    = v3_req

[dn]
C  = US
ST = State
L  = City
O  = Infrastructure
CN = ollama-server.local

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = ollama-server.local
IP.1  = 172.16.10.109
EOF
```

## Generate private key and public certificate valid for 365 days

```
sudo openssl req -x509 -nodes -days 2048 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/ollama.key \
  -out /etc/nginx/ssl/ollama.crt \
  -config ollama_ssl.cnf

```

## Configure Nginx Reverse Proxy the have https port 443 with the self signed cert (/etc/nginx/conf.d/ollama.conf):

```
Nginxserver {
    listen 443 ssl;
    server_name ollama-server.local 192.168.1.100;

    ssl_certificate     /etc/nginx/ssl/ollama.crt;
    ssl_certificate_key /etc/nginx/ssl/ollama.key;

    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Increase timeout limits for longer LLM generation calls
    proxy_read_timeout  600s;
    proxy_connect_timeout 600s;
    proxy_send_timeout  600s;

    location / {
        proxy_pass http://127.0.0.1:11434;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Restart Nginx and verify execution

```
sudo nginx -t
sudo systemctl restart nginx

curl -k https://localhost/api/tags
```

# install dbms_cloud

```
$ORACLE_HOME/perl/bin/perl $ORACLE_HOME/rdbms/admin/catcon.pl \
  -u sys/Password123 \
  -force_pdb_mode 'READ WRITE' \
  -b dbms_cloud_install \
  -d $ORACLE_HOME/rdbms/admin/ \
  -l /tmp \
  catclouduser.sql

$ORACLE_HOME/perl/bin/perl $ORACLE_HOME/rdbms/admin/catcon.pl \
  -u sys/Password123 \
  -force_pdb_mode 'READ WRITE' \
  -b dbms_cloud_install \
  -d $ORACLE_HOME/rdbms/admin/ \
  -l /tmp \
  dbms_cloud_install.sql
```

# Configure Oracle Database Wallet for SSL/TLS 

Copy ollama.crt to the database server (e.g., /u01/app/oracle/admin/wallets/ollama_wallet/ollama.crt).

Create the Auto-Login Wallet using orapki

```
mkdir -p /u01/app/oracle/admin/wallets/ollama_wallet
```

# Create new wallet

```
orapki wallet create -wallet /u01/app/oracle/admin/wallets/ollama_wallet -pwd "WalletPassword123#" -auto_login
```

# Import self-signed certificate

```
orapki wallet add -wallet /u01/app/oracle/admin/wallets/ollama_wallet -pwd "WalletPassword123#" \
  -trusted_cert -cert /u01/app/oracle/admin/wallets/ollama_wallet/ollama.crt
```

Verify wallet content:

```
orapki wallet display -wallet /u01/app/oracle/admin/wallets/ollama_wallet
```

Ensure Oracle process owner owns the wallet files

```
chown -R oracle:oinstall /u01/app/oracle/admin/wallets/ollama_wallet
chmod 600 /u01/app/oracle/admin/wallets/ollama_wallet/*
```

# Configure Network Access Control (ACL) in Oracle 26ai

Grant the application database user network privileges to initiate outbound HTTP calls to the Nginx endpoint.SQL-- Connect as SYSDBA

```
sqlplus / AS SYSDBA;
```

set the ssl_wallet property at the CDB level to allow the self signed certificate

```
ALTER DATABASE PROPERTY SET
  SSL_WALLET='/u01/app/oracle/admin/wallets/ollama_wallet';
```

# Create or grant required permissions to application schema user (e.g., SH,C##CLOUD$SERVICE)

```
alter session set container=name_of_pdb;

GRANT DB_DEVELOPER_ROLE TO SH;
GRANT EXECUTE ON DBMS_CLOUD TO SH;
GRANT EXECUTE ON DBMS_CLOUD_AI TO SH;

GRANT DB_DEVELOPER_ROLE TO C##CLOUD$SERVICE;
GRANT EXECUTE ON DBMS_CLOUD TO C##CLOUD$SERVICE;
GRANT EXECUTE ON DBMS_CLOUD_AI TO C##CLOUD$SERVICE;
```

as SH grant select on all tables to C##CLOUD$SERVICE

```
select 'grant select on '||table_name||' to C##CLOUD$SERVICE;' from user_tables;
```

## ACLs for C##CLOUD$SERVICE

```
BEGIN
  DBMS_NETWORK_ACL_ADMIN.APPEND_HOST_ACE(
    host => 'ollama-server.local',
    ace  => XS$ACE_TYPE(
      privilege_list => XS$NAME_LIST('resolve'),
      principal_name => 'C##CLOUD$SERVICE',
      principal_type => XS_ACL.PTYPE_DB
    )
  );
END;
/

BEGIN
  DBMS_NETWORK_ACL_ADMIN.APPEND_HOST_ACE(
    host       => 'ollama-server.local',
    lower_port => 443,
    upper_port => 443,
    ace        => XS$ACE_TYPE(
      privilege_list => XS$NAME_LIST('connect'),
      principal_name => 'C##CLOUD$SERVICE',
      principal_type => XS_ACL.PTYPE_DB
    )
  );
END;
/
```

## ACLs for SH

```
BEGIN
  DBMS_NETWORK_ACL_ADMIN.APPEND_HOST_ACE(
    host => 'ollama-server.local',
    ace  => XS$ACE_TYPE(
      privilege_list => XS$NAME_LIST('resolve'),
      principal_name => 'SH',
      principal_type => XS_ACL.PTYPE_DB
    )
  );
END;
/

BEGIN
  DBMS_NETWORK_ACL_ADMIN.APPEND_HOST_ACE(
    host       => 'ollama-server.local',
    lower_port => 443,
    upper_port => 443,
    ace        => XS$ACE_TYPE(
      privilege_list => XS$NAME_LIST('connect'),
      principal_name => 'SH',
      principal_type => XS_ACL.PTYPE_DB
    )
  );
END;
/
```

before proceeding, check that ssl works

connect as sh in your pdb and test this

```
SELECT UTL_HTTP.REQUEST(
         'https://ollama-server.local/api/tags',
         NULL,
         'file:/u01/app/oracle/admin/wallets/ollama_wallet',
         NULL
       ) AS response
FROM dual;
```

# Configure Credential and Select AI Profile in 26ai

```
Connect as the target DB user
CONN sh/sh@@orcl26ai;
```

## Create a dummy or standard token credential required by DBMS_CLOUD_AI syntax

```
BEGIN
  DBMS_CLOUD.CREATE_CREDENTIAL(
    credential_name => 'OLLAMA_CRED',
    username       => 'NONE',
    password        => 'NONE'
  );
END;
/
```

## Create the Select AI Profile pointing to Ollama API via Nginx

please note the model. This is what will be loaded in ollama so make sure this is a valide ollama model

```
BEGIN
  DBMS_CLOUD_AI.CREATE_PROFILE(
    profile_name => 'OLLAMA_LOCAL_PROFILE',
    attributes   => '{
      "provider": "openai",
      "credential_name": "OLLAMA_CRED",
      "provider_endpoint": "https://ollama-server.local/v1",
      "model": "qwen2.5-coder:7b",
      "temperature": 0.1,
      "object_list": [{"owner": "SH"}]
    }'
  );
END;
/
```

# test SELECT AI using sqlplus

## Set the AI profile active for the current session

```
EXEC DBMS_CLOUD_AI.SET_PROFILE('OLLAMA_LOCAL_PROFILE');
```

## Test and Verify Select AI Setup:SQL / Natural Language Query

-- Test Natural Language to SQL query translation

```
SELECT AI showsql how many customers are registered;
```

-- Run natural language query directly against database tables

```
SELECT AI run prompt how many total orders were placed last month;
```

-- Validate query explanation using local LLM reasoning

```
SELECT AI narratesql list top 5 revenue producing items;
SELECT AI run prompt how many customers are registered;
SELECT AI run prompt how many customers per country name;
SELECT AI run prompt how many total orders were placed per year;
```

# flask app for seach

you can run the flask app included, adjust as required
