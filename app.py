import os
import ipaddress
import math
import time
import json
import uuid
import logging
from typing import Optional, Tuple, List, Dict

import psycopg2
import psycopg2.extras
from psycopg2 import sql
from psycopg2 import errors as pg_errors

from fastapi import FastAPI, HTTPException, Request, Header, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# -------------------------------------------------------------------
# Logging Configuration
# -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------
DB_DSN = (
    f"dbname={os.getenv('POSTGRES_DB')}"
    f" user={os.getenv('POSTGRES_USER')}"
    f" password={os.getenv('POSTGRES_PASSWORD')}"
    f" host={os.getenv('POSTGRES_HOST','postgres')}"
)
MAX_RETRIES = 5
PRIMARY_POOL = ipaddress.IPv4Network("10.0.0.0/16")
CGNAT_POOL = ipaddress.IPv4Network("100.64.0.0/10")

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    logger.warning("API_KEY not set in environment variables")

app = FastAPI(
    title="IPAM API", 
    description="Automatic CIDR allocation with smart subnet sizing",
    version="1.0.0"
)

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static/template (ensure dirs exist in the container)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# -------------------------------------------------------------------
# Server-generated Request ID (clients cannot set it)
# -------------------------------------------------------------------
REQ_HDR = "X-Request-Id"

@app.middleware("http")
async def force_request_id(request: Request, call_next):
    rid = uuid.uuid4().hex  # always new, ignore any client header
    request.state.request_id = rid
    
    # Log incoming requests
    logger.info(f"Request {rid}: {request.method} {request.url.path}")
    
    response = await call_next(request)
    response.headers[REQ_HDR] = rid
    
    # Log response status
    logger.info(f"Request {rid}: Response {response.status_code}")
    
    return response

# -------------------------------------------------------------------
# Auth
# -------------------------------------------------------------------
def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")):
    if not API_KEY:
        logger.error("API key not configured on server")
        raise HTTPException(status_code=500, detail={"code": "SERVER_ERROR", "message": "API key not configured", "details": None})
    if x_api_key != API_KEY:
        logger.warning(f"Invalid API key attempt: {x_api_key[:8]}...")
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid API Key", "details": None})
    return True

# -------------------------------------------------------------------
# Models (unchanged from your original)
# -------------------------------------------------------------------
class VPC(BaseModel):
    name: str

class Labels(BaseModel):
    environment: Optional[str] = Field(None, description="dev|stage|prod")
    region: Optional[str] = Field(None, description="e.g., us-east")

    @validator("environment")
    def env_ok(cls, v):
        if v is None:
            return v
        if v not in {"dev", "stage", "prod"}:
            raise ValueError("environment must be one of dev, stage, prod")
        return v

    @validator("region")
    def region_ok(cls, v):
        if v is None:
            return v
        v2 = v.strip()
        if not v2:
            raise ValueError("region cannot be empty")
        return v2

    def to_jsonb(self) -> Dict[str, str]:
        out = {}
        if self.environment is not None:
            out["environment"] = self.environment
        if self.region is not None:
            out["region"] = self.region
        return out

class AllocationRequest(BaseModel):
    vpc: str
    hosts: Optional[int] = Field(None, description="1..4000")
    prefix_length: Optional[int] = Field(None, ge=20, le=26, description="/20.. /26")
    labels: Optional[Labels] = None

    class Config:
        schema_extra = {
            "examples": [
                {"vpc": "production", "hosts": 500, "labels": {"environment": "prod", "region": "us-east"}},
                {"vpc": "development", "prefix_length": 24},
            ]
        }

class ReassignRequest(BaseModel):
    new_vpc_name: str = Field(min_length=1)

# -------------------------------------------------------------------
# Error shaping
# -------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    # Log errors for debugging
    if exc.status_code >= 500:
        logger.error(f"Server error: {exc.detail}")
    elif exc.status_code >= 400:
        logger.warning(f"Client error: {exc.detail}")
        
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "message": str(exc.detail), "details": None})

# -------------------------------------------------------------------
# DB helpers
# -------------------------------------------------------------------
def db():
    try:
        conn = psycopg2.connect(DB_DSN)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    except psycopg2.Error as e:
        logger.error(f"Database connection failed: {e}")
        raise HTTPException(500, {"code": "DB_CONNECTION_ERROR", "message": "Database unavailable", "details": str(e)})

def ensure_constraints():
    ddl = """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='allocations_primary_unique') THEN
            ALTER TABLE allocations ADD CONSTRAINT allocations_primary_unique UNIQUE (primary_cidr);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='allocations_cgnat_unique') THEN
            ALTER TABLE allocations ADD CONSTRAINT allocations_cgnat_unique UNIQUE (cgnat_cidr);
        END IF;
    END$$;
    """
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        logger.info("Database constraints ensured")
    except Exception as e:
        logger.error(f"Failed to ensure database constraints: {e}")

@app.on_event("startup")
def _startup():
    logger.info("Starting IPAM API service")
    try:
        ensure_constraints()
        logger.info("IPAM API startup completed successfully")
    except Exception as e:
        logger.error(f"Startup failed: {e}")

# -------------------------------------------------------------------
# Sizing (unchanged)
# -------------------------------------------------------------------
def hosts_to_prefix_length(hosts: int) -> int:
    needed_addresses = hosts + 5  # policy reserve
    host_bits = math.ceil(math.log2(max(needed_addresses, 1)))
    prefix_length = 32 - host_bits
    result = max(20, min(26, prefix_length))
    logger.debug(f"Calculated prefix /{result} for {hosts} hosts")
    return result

def usable_count(prefix_len: int) -> int:
    return (2 ** (32 - prefix_len)) - 5  # matches policy

# -------------------------------------------------------------------
# Overlap checks with improved logging
# -------------------------------------------------------------------
CHECK_OVERLAP_SQL = """
SELECT {column}::text AS cidr
FROM {table}
WHERE {column} && %s::cidr
LIMIT %s
"""

def find_overlaps(cur, table: str, column: str, candidate_cidr: str, limit: int = 20) -> List[str]:
    q = sql.SQL(CHECK_OVERLAP_SQL).format(table=sql.Identifier(table), column=sql.Identifier(column))
    cur.execute(q, (candidate_cidr, limit))
    rows = cur.fetchall()
    overlaps = [r["cidr"] for r in rows]
    if overlaps:
        logger.debug(f"Found {len(overlaps)} overlaps for {candidate_cidr} in {table}.{column}")
    return overlaps

def subnet_is_free(cur, table: str, column: str, candidate_cidr: str) -> bool:
    q = sql.SQL("SELECT 1 FROM {t} WHERE {c} && %s::cidr LIMIT 1").format(t=sql.Identifier(table), c=sql.Identifier(column))
    cur.execute(q, (candidate_cidr,))
    is_free = cur.fetchone() is None
    logger.debug(f"Subnet {candidate_cidr} is {'free' if is_free else 'occupied'} in {table}.{column}")
    return is_free

def next_free_in_pool(cur, pool: ipaddress.IPv4Network, prefix_len: int, table: str, column: str) -> Tuple[str, List[str]]:
    logger.debug(f"Searching for free /{prefix_len} subnet in {pool}")
    start = int(pool.network_address)
    step = 2 ** (32 - prefix_len)
    pool_end = int(pool.broadcast_address)
    current = start
    mod = current % step
    if mod != 0:
        current += (step - mod)
        
    candidates_checked = 0
    while current + step - 1 <= pool_end:
        candidate = ipaddress.IPv4Network((current, prefix_len))
        candidates_checked += 1
        
        if candidate.subnet_of(pool):
            cidr = str(candidate)
            if subnet_is_free(cur, table, column, cidr):
                logger.info(f"Found free subnet {cidr} after checking {candidates_checked} candidates")
                return cidr, []
        current += step
        
        # Log progress for long searches
        if candidates_checked % 100 == 0:
            logger.debug(f"Checked {candidates_checked} candidates, still searching...")
    
    logger.warning(f"No free /{prefix_len} subnets found in {pool} after checking {candidates_checked} candidates")
    return "", ["no_free_block"]

def find_next_available_subnets_tx(cur, prefix_length: int) -> Tuple[str, str, int, Dict[str, List[str]]]:
    logger.info(f"Finding available subnets for /{prefix_length}")
    diag: Dict[str, List[str]] = {}
    
    primary_cidr, _ = next_free_in_pool(cur, PRIMARY_POOL, prefix_length, "allocations", "primary_cidr")
    if not primary_cidr:
        diag["primary_conflicts"] = ["exhausted"]
        logger.error(f"No available /{prefix_length} in {PRIMARY_POOL.with_prefixlen}")
        raise HTTPException(status_code=400, detail={"code": "NO_SPACE", "message": f"No available /{prefix_length} in {PRIMARY_POOL.with_prefixlen}", "details": diag})
    
    cgnat_prefix = prefix_length - 5
    if cgnat_prefix < 0:
        logger.error(f"Invalid CGNAT prefix calculation: {prefix_length} - 5 = {cgnat_prefix}")
        raise HTTPException(status_code=400, detail={"code": "BAD_POLICY", "message": "Computed CGNAT prefix invalid", "details": None})
    
    cgnat_cidr, _ = next_free_in_pool(cur, CGNAT_POOL, cgnat_prefix, "allocations", "cgnat_cidr")
    if not cgnat_cidr:
        diag["cgnat_conflicts"] = ["exhausted"]
        logger.error(f"No available /{cgnat_prefix} in {CGNAT_POOL.with_prefixlen}")
        raise HTTPException(status_code=400, detail={"code": "NO_SPACE", "message": f"No available /{cgnat_prefix} in {CGNAT_POOL.with_prefixlen}", "details": diag})
    
    logger.info(f"Successfully found subnets: primary={primary_cidr}, cgnat={cgnat_cidr}")
    return primary_cidr, cgnat_cidr, cgnat_prefix, diag

# -------------------------------------------------------------------
# Routes (key ones with logging improvements)
# -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/healthz")
def healthz():
    return {"status": "ok", "timestamp": time.time()}

@app.get("/readyz")
def readyz():
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            _ = cur.fetchone()
        logger.debug("Readiness check passed")
        return {"status": "ready", "timestamp": time.time()}
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        raise HTTPException(status_code=500, detail={"code": "SERVICE_UNAVAILABLE", "message": "Database not ready", "details": str(e)})

# ... (rest of your routes remain the same, just add this logging improvement to the allocate endpoint)

@app.post("/allocate", dependencies=[Depends(verify_api_key)])
def allocate(req: Request, payload: AllocationRequest, dry_run: bool = Query(False)):
    request_id = req.state.request_id
    logger.info(f"Request {request_id}: Starting allocation for VPC '{payload.vpc}', hosts={payload.hosts}, prefix={payload.prefix_length}")
    
    # input validation
    if payload.hosts is not None and payload.prefix_length is not None:
        raise HTTPException(400, {"code": "BAD_REQUEST", "message": "Specify either 'hosts' or 'prefix_length', not both", "details": None})
    if payload.hosts is None and payload.prefix_length is None:
        raise HTTPException(400, {"code": "BAD_REQUEST", "message": "Must specify either 'hosts' or 'prefix_length'", "details": None})
    labels_json = (payload.labels or Labels()).to_jsonb()

    if payload.hosts is not None:
        if payload.hosts < 1 or payload.hosts > 4000:
            raise HTTPException(400, {"code": "BAD_REQUEST", "message": "hosts must be between 1 and 4000", "details": None})
        prefix_length = hosts_to_prefix_length(payload.hosts)
        logger.info(f"Request {request_id}: Calculated prefix length /{prefix_length} for {payload.hosts} hosts")
    else:
        prefix_length = int(payload.prefix_length)  # 20..26 by model
        logger.info(f"Request {request_id}: Using specified prefix length /{prefix_length}")

    # transactional allocation with retries
    for attempt in range(1, MAX_RETRIES + 1):
        logger.debug(f"Request {request_id}: Allocation attempt {attempt}/{MAX_RETRIES}")
        try:
            with db() as conn:
                conn.set_session(isolation_level="SERIALIZABLE", readonly=False, autocommit=False)
                with conn.cursor() as cur:
                    # ensure vpc row
                    cur.execute("INSERT INTO vpcs(name) VALUES(%s) ON CONFLICT DO NOTHING", (payload.vpc,))

                    # compute candidates
                    primary_cidr, cgnat_cidr, cgnat_prefix, _diag = find_next_available_subnets_tx(cur, prefix_length)

                    if dry_run:
                        logger.info(f"Request {request_id}: Dry run successful - would allocate {primary_cidr}/{cgnat_cidr}")
                        return {
                            "ok": True,
                            "dry_run": True,
                            "vpc": payload.vpc,
                            "primary_cidr": primary_cidr,
                            "cgnat_cidr": cgnat_cidr,
                            "primary_subnet_size": f"/{prefix_length}",
                            "cgnat_subnet_size": f"/{cgnat_prefix}",
                            "usable_primary": usable_count(prefix_length),
                            "usable_cgnat": usable_count(cgnat_prefix),
                            "requested_hosts": payload.hosts,
                            "requested_prefix": payload.prefix_length,
                            "labels": labels_json,
                        }

                    # insert allocation with server-minted request_id
                    server_request_id = req.state.request_id  # from middleware
                    cur.execute(
                        """
                        INSERT INTO allocations
                          (vpc_id, primary_cidr, cgnat_cidr, requested_hosts, requested_prefix, labels, request_id)
                        VALUES
                          ((SELECT id FROM vpcs WHERE name=%s), %s::cidr, %s::cidr, %s, %s, %s::jsonb, %s)
                        RETURNING id
                        """,
                        (
                            payload.vpc,
                            primary_cidr,
                            cgnat_cidr,
                            payload.hosts,
                            payload.prefix_length,
                            json.dumps(labels_json),
                            server_request_id,
                        ),
                    )
                    row = cur.fetchone()
                    alloc_id = row["id"]
                conn.commit()
                logger.info(f"Request {request_id}: Successfully created allocation {alloc_id}")

            return {
                "ok": True,
                "allocation_id": alloc_id,
                "vpc": payload.vpc,
                "primary_cidr": primary_cidr,
                "cgnat_cidr": cgnat_cidr,
                "primary_subnet_size": f"/{prefix_length}",
                "cgnat_subnet_size": f"/{cgnat_prefix}",
                "usable_primary": usable_count(prefix_length),
                "usable_cgnat": usable_count(cgnat_prefix),
                "requested_hosts": payload.hosts,
                "requested_prefix": payload.prefix_length,
                "labels": labels_json,
                "request_id": server_request_id,
            }

        except (pg_errors.SerializationFailure, pg_errors.DeadlockDetected) as e:
            logger.warning(f"Request {request_id}: Serialization/deadlock error on attempt {attempt}: {e}")
            if attempt == MAX_RETRIES:
                raise HTTPException(503, {"code": "RETRY_EXHAUSTED", "message": "Allocation contention", "details": None})
            time.sleep(0.05 * attempt)
            continue
        except pg_errors.UniqueViolation as e:
            logger.warning(f"Request {request_id}: Unique violation on attempt {attempt}: {e}")
            if attempt == MAX_RETRIES:
                raise HTTPException(503, {"code": "RETRY_EXHAUSTED", "message": "Allocation race", "details": str(e)})
            time.sleep(0.05 * attempt)
            continue
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Request {request_id}: Unexpected error on attempt {attempt}: {e}")
            raise HTTPException(500, {"code": "INTERNAL_ERROR", "message": "Internal error", "details": str(e)})

    raise HTTPException(500, {"code": "INTERNAL_ERROR", "message": "Unexpected retry failure", "details": None})

# Keep all your other existing routes unchanged
@app.get("/allocations", dependencies=[Depends(verify_api_key)])
def list_allocations(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    vpc: Optional[str] = None,
):
    with db() as conn, conn.cursor() as cur:
        if vpc:
            cur.execute("SELECT COUNT(*) AS c FROM allocations a JOIN vpcs v ON a.vpc_id=v.id WHERE v.name=%s", (vpc,))
        else:
            cur.execute("SELECT COUNT(*) AS c FROM allocations")
        total = int(cur.fetchone()["c"])

        if vpc:
            cur.execute(
                """
                SELECT v.name AS vpc,
                       a.id AS allocation_id,
                       a.primary_cidr::text,
                       (power(2, 32 - masklen(a.primary_cidr))::bigint - 5) AS usable_primary,
                       a.cgnat_cidr::text,
                       (power(2, 32 - masklen(a.cgnat_cidr))::bigint - 5) AS usable_cgnat,
                       a.requested_hosts,
                       a.requested_prefix,
                       a.labels,
                       a.request_id::text,
                       a.created_at
                FROM allocations a
                JOIN vpcs v ON a.vpc_id = v.id
                WHERE v.name=%s
                ORDER BY v.name, a.primary_cidr
                LIMIT %s OFFSET %s
                """,
                (vpc, limit, offset),
            )
        else:
            cur.execute(
                """
                SELECT v.name AS vpc,
                       a.id AS allocation_id,
                       a.primary_cidr::text,
                       (power(2, 32 - masklen(a.primary_cidr))::bigint - 5) AS usable_primary,
                       a.cgnat_cidr::text,
                       (power(2, 32 - masklen(a.cgnat_cidr))::bigint - 5) AS usable_cgnat,
                       a.requested_hosts,
                       a.requested_prefix,
                       a.labels,
                       a.request_id::text,
                       a.created_at
                FROM allocations a
                JOIN vpcs v ON a.vpc_id = v.id
                ORDER BY v.name, a.primary_cidr
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
        rows = cur.fetchall()
        for r in rows:
            if r["created_at"]:
                r["created_at"] = r["created_at"].isoformat()
        return {"total_count": total, "limit": limit, "offset": offset, "items": rows}

@app.post("/vpcs", dependencies=[Depends(verify_api_key)])
def create_vpc(v: VPC):
    with db() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO vpcs(name) VALUES(%s) ON CONFLICT DO NOTHING", (v.name,))
        logger.info(f"Created/ensured VPC: {v.name}")
    return {"ok": True}

@app.put("/allocations/{allocation_id}", dependencies=[Depends(verify_api_key)])
def update_allocation_vpc(allocation_id: int, payload: ReassignRequest):
    new_vpc_name = payload.new_vpc_name.strip()
    if not new_vpc_name:
        raise HTTPException(400, {"code": "BAD_REQUEST", "message": "new_vpc_name is required", "details": None})

    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT vpc_id FROM allocations WHERE id=%s", (allocation_id,))
        allocation = cur.fetchone()
        if not allocation:
            raise HTTPException(404, {"code": "NOT_FOUND", "message": f"Allocation {allocation_id} not found", "details": None})

        cur.execute("INSERT INTO vpcs(name) VALUES(%s) ON CONFLICT DO NOTHING", (new_vpc_name,))
        cur.execute("SELECT id FROM vpcs WHERE name=%s", (new_vpc_name,))
        vpc_id = cur.fetchone()["id"]
        cur.execute("UPDATE allocations SET vpc_id=%s WHERE id=%s", (vpc_id, allocation_id))
        logger.info(f"Moved allocation {allocation_id} to VPC '{new_vpc_name}'")

    return {"ok": True, "allocation_id": allocation_id, "new_vpc_name": new_vpc_name}

@app.delete("/allocations/{allocation_id}", dependencies=[Depends(verify_api_key)])
def delete_allocation(allocation_id: int):
    with db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM allocations WHERE id=%s", (allocation_id,))
        affected = cur.rowcount
    if affected == 0:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": f"Allocation {allocation_id} not found", "details": None})
    logger.info(f"Deleted allocation {allocation_id}")
    return {"ok": True, "deleted": True}

@app.delete("/vpcs/{name}", dependencies=[Depends(verify_api_key)])
def delete_vpc(name: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM vpcs WHERE name=%s", (name,))
        affected = cur.rowcount
    logger.info(f"Deleted VPC '{name}' (affected {affected} rows)")
    return {"ok": True, "deleted": affected > 0}

@app.get("/calculate", dependencies=[Depends(verify_api_key)])
def calculate_subnet_info(hosts: int = None, prefix_length: int = None):
    if hosts is not None and prefix_length is not None:
        raise HTTPException(400, {"code": "BAD_REQUEST", "message": "Specify either hosts or prefix_length, not both", "details": None})
    if hosts is None and prefix_length is None:
        raise HTTPException(400, {"code": "BAD_REQUEST", "message": "Must specify either hosts or prefix_length", "details": None})

    if hosts is not None:
        if hosts < 1 or hosts > 4000:
            raise HTTPException(400, {"code": "BAD_REQUEST", "message": "hosts must be between 1 and 4000", "details": None})
        prefix_length = hosts_to_prefix_length(hosts)

    cgnat_prefix = prefix_length - 5
    if cgnat_prefix < 0:
        raise HTTPException(400, {"code": "BAD_POLICY", "message": "Computed CGNAT prefix invalid", "details": None})

    return {
        "requested_hosts": hosts,
        "requested_prefix": prefix_length,
        "calculated_prefix": prefix_length,
        "primary_subnet_size": f"/{prefix_length}",
        "cgnat_subnet_size": f"/{cgnat_prefix}",
        "usable_primary_ips": usable_count(prefix_length),
        "usable_cgnat_ips": usable_count(cgnat_prefix),
        "total_addresses_primary": 2 ** (32 - prefix_length),
        "total_addresses_cgnat": 2 ** (32 - cgnat_prefix),
    }

# utility for masklen if needed elsewhere
def masklen(cidr_text: str) -> int:
    return ipaddress.IPv4Network(cidr_text).prefixlen