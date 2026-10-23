import asyncio
import asyncpg
import uuid
import hashlib
import json
from dotenv import dotenv_values

ss_env = dotenv_values('/Users/karanagg/Desktop/Projects/search-sphere/.env')
qc_env = dotenv_values('/Users/karanagg/Desktop/Projects/Quick-Clinic/.env.search-sphere-key.local')

db_url = ss_env.get('DATABASE_URL').replace('+asyncpg', '')
api_key = qc_env.get('SEARCH_SPHERE_API_KEY')
client_id = qc_env.get('SEARCH_SPHERE_CLIENT_ID', 'quick_clinic')

key_hash = hashlib.sha256(api_key.encode()).hexdigest()
prefix = api_key[:12]

async def main():
    conn = await asyncpg.connect(db_url)
    
    # 1. Update alembic_version
    await conn.execute("UPDATE alembic_version SET version_num = '007_medical_scope';")
    ver = await conn.fetchval('SELECT version_num FROM alembic_version')
    print('Alembic version in DB:', ver)
    
    # 2. Check and upsert service_clients
    existing = await conn.fetchrow('SELECT id FROM service_clients WHERE client_id = $1', client_id)
    scopes = json.dumps(['documents:read', 'documents:write', 'documents:delete', 'search:execute', 'answers:generate', 'collections:manage'])
    tenants = json.dumps(['*'])
    
    if existing:
        await conn.execute('''
            UPDATE service_clients 
            SET api_key_hash = $1, api_key_prefix = $2, status = 'active', allowed_scopes = $3::json, authorized_tenants = $4::json, updated_at = NOW()
            WHERE client_id = $5
        ''', key_hash, prefix, scopes, tenants, client_id)
        print(f'Updated existing client {client_id}')
    else:
        new_id = str(uuid.uuid4())
        await conn.execute('''
            INSERT INTO service_clients (id, client_id, name, api_key_hash, api_key_prefix, status, allowed_scopes, authorized_tenants, rate_limit_per_minute, created_at, updated_at)
            VALUES ($1, $2, 'Quick-Clinic Healthcare System', $3, $4, 'active', $5::json, $6::json, 600, NOW(), NOW())
        ''', new_id, client_id, key_hash, prefix, scopes, tenants)
        print(f'Inserted new client {client_id}')
        
    client_row = await conn.fetchrow('SELECT client_id, api_key_prefix, status, allowed_scopes, authorized_tenants FROM service_clients WHERE client_id = $1', client_id)
    print('Verified client_row:', dict(client_row))
    await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
