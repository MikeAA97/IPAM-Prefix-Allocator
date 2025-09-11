## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

# IPAM - IP Address Management System

A FastAPI-based automatic IP address allocation system. Designed with a K8s Node:Pod Relationship. Built using Claude and a ton of late-night coffee.

## Features

- **Automatic CIDR allocation** from predefined pools (10.0.0.0/16 primary, 100.64.0.0/10 CGNAT)
- **Smart subnet sizing** - Specify a Node Range between /20 (~4K Nodes) to /26 (~60 Nodes) supporting 32 pods per node all in unique IP Spaces for cross-cluster communication support.
- **Overlap prevention** using PostgreSQL's GIST exclusion constraints
- **Web dashboard** for visualization and management
- **RESTful API** with OpenAPI documentation
- **Kubernetes deployment** ready

## Architecture

```
┌─────────────────--┐    ┌──────────────────┐    ┌────────────────┐
│   Web Dashboard   │────│   FastAPI App    │────│   PostgreSQL   │
│   (HTML/JS/CSS)   │    │                  │    └────────────────┘
└─────────────────--┘    │  - Auto CIDR     │
                         │  - Validation    │
                         │  - Overlap check │
                         └──────────────────┘
```

The system allocates paired CIDRs:
- **Primary CIDR**: /20-/26 from 10.0.0.0/16 pool
- **CGNAT CIDR**: /15-/21 from 100.64.0.0/10 pool (always /5 larger than primary) to support 32 pods per Node

## Quick Start

### Local Development

1. **Clone and setup**:
```bash
git clone https://github.com/MikeAA97/IPAM-Prefix-Allocator
cd IPAM-Prefix-Allocator
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
openssl rand -hex 8 # Generates an API-Token for API Usage
```
1. **Start services with Docker Compose**:
```bash
# Ensure the API-Key generated in Step 1 is placed in the ipam-api service
docker-compose up -d
```

2. **Access the application**:
- Dashboard: http://localhost:8000
- API Documentation: http://localhost:8000/docs

### Kubernetes Deployment

1. **Create local cluster**:
`./k8s-setup.sh`

2. **Access**:
- Dashboard: http://localhost:8080
- Input generated API-Key

## API Usage

### Allocate by Host Count
```bash
curl -X POST "http://localhost:8000/allocate" \
  -H "X-API-Key: <API-Key>" \
  -H "Content-Type: application/json" \
  -d '{
    "vpc": "production",
    "hosts": 500,
    "labels": {"environment": "prod", "region": "us-east"}
  }'
```

### Allocate by Prefix Length
```bash
curl -X POST "http://localhost:8000/allocate" \
  -H "X-API-Key: <API-Key>" \
  -H "Content-Type: application/json" \
  -d '{
    "vpc": "development", 
    "prefix_length": 24
  }'
```

### List Allocations
```bash
curl "http://localhost:8000/allocations" \
  -H "X-API-Key: <API-Key>"
```

## Subnet Sizing Logic

The system automatically calculates optimal subnet sizes:

| Hosts Requested | Calculated Prefix | Usable IPs | CGNAT Prefix |
|-----------------|-------------------|------------|--------------|
| 1-59           | /26               | 59         | /21          |
| 60-123         | /25               | 123        | /20          |
| 124-251        | /24               | 251        | /19          |
| 252-507        | /23               | 507        | /18          |
| 508-1019       | /22               | 1019       | /17          |
| 1020-2043      | /21               | 2043       | /16          |
| 2044-4091      | /20               | 4091       | /15          |

**Policy Reserve**: 5 IPs reserved per subnet (network, broadcast, gateway, DNS, spare) - Based on AWS' cut per subnet.

## Database Schema

### Core Tables
- `vpcs`: VPC definitions with unique names
- `allocations`: CIDR allocations with metadata and constraints

### Key Constraints
- **Overlap Prevention**: GIST exclusion constraints prevent CIDR overlaps
- **Pool Containment**: Primary CIDRs must be within 10.0.0.0/16, CGNAT within 100.64.0.0/10
- **Size Relationship**: CGNAT prefix always 5 bits smaller than primary
- **Prefix Bounds**: Primary subnets limited to /20-/26 range

## Configuration

### Environment Variables
```bash
# Database
POSTGRES_HOST=postgres
POSTGRES_DB=ipam
POSTGRES_USER=ipam
POSTGRES_PASSWORD=ipam123

# API Security
API_KEY=<API-Key>
```

### Pools Configuration
- **Primary Pool**: 10.0.0.0/16 (65,536 addresses)
- **CGNAT Pool**: 100.64.0.0/10 (4,194,304 addresses)
  - A Note that I want to go back and make this VRF-Aware. In a sense that each environment gets it's own copy of 100.64.0.0/10. The CGNAT space is the botlleneck in terms of IP Exhaustion currently, and that will help to partially alleviate it.

## Development

### Running Tests
```bash
# Install test dependencies
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install pytest

# Run tests
python3.12 -m pytest  
```

## Future nice-to-haves

- A more Netbox like visualization.
- VRFs for CGNAT Range.
- Multi-Stage Docker Builds to make final artifacts a bit smaller.
- Resource constraints for K8s deployments to align with best practices.
- More CSS to make the webpage look a bit more fun.
