#!/usr/bin/env python3
"""
IPAM CLI Tool - Simple command line interface for the IPAM API
Usage: python ipam_cli.py [command] [options]
"""
import argparse
import json
import sys
import requests
from typing import Optional

class IPAMClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'X-API-Key': api_key,
            'Content-Type': 'application/json'
        })

    def _request(self, method: str, endpoint: str, data: dict = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
            if hasattr(e.response, 'json'):
                try:
                    error_detail = e.response.json()
                    print(f"Details: {error_detail}")
                except:
                    pass
            sys.exit(1)

    def create_vpc(self, name: str) -> dict:
        """Create a new VPC"""
        return self._request('POST', '/vpcs', {'name': name})

    def allocate(self, vpc: str, hosts: Optional[int] = None, 
                prefix_length: Optional[int] = None, 
                environment: Optional[str] = None,
                region: Optional[str] = None) -> dict:
        """Create a new allocation"""
        data = {'vpc': vpc}
        
        if hosts is not None:
            data['hosts'] = hosts
        elif prefix_length is not None:
            data['prefix_length'] = prefix_length
        else:
            raise ValueError("Must specify either hosts or prefix_length")
            
        if environment or region:
            data['labels'] = {}
            if environment:
                data['labels']['environment'] = environment
            if region:
                data['labels']['region'] = region

        return self._request('POST', '/allocate', data)

    def list_allocations(self, vpc: Optional[str] = None, limit: int = 50) -> dict:
        """List allocations"""
        params = f"?limit={limit}"
        if vpc:
            params += f"&vpc={vpc}"
        return self._request('GET', f'/allocations{params}')

    def delete_allocation(self, allocation_id: int) -> dict:
        """Delete an allocation"""
        return self._request('DELETE', f'/allocations/{allocation_id}')

    def calculate(self, hosts: Optional[int] = None, 
                 prefix_length: Optional[int] = None) -> dict:
        """Calculate subnet information"""
        params = []
        if hosts is not None:
            params.append(f"hosts={hosts}")
        elif prefix_length is not None:
            params.append(f"prefix_length={prefix_length}")
        else:
            raise ValueError("Must specify either hosts or prefix_length")
            
        query_string = "?" + "&".join(params)
        return self._request('GET', f'/calculate{query_string}')

def main():
    parser = argparse.ArgumentParser(description='IPAM CLI Tool')
    parser.add_argument('--url', default='http://localhost:8000', 
                       help='IPAM API base URL')
    parser.add_argument('--api-key', required=True,
                       help='API key for authentication')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # VPC commands
    vpc_parser = subparsers.add_parser('create-vpc', help='Create a new VPC')
    vpc_parser.add_argument('name', help='VPC name')
    
    # Allocation commands
    alloc_parser = subparsers.add_parser('allocate', help='Create allocation')
    alloc_parser.add_argument('vpc', help='VPC name')
    alloc_group = alloc_parser.add_mutually_exclusive_group(required=True)
    alloc_group.add_argument('--hosts', type=int, help='Number of hosts needed')
    alloc_group.add_argument('--prefix', type=int, help='Prefix length (/20-/26)')
    alloc_parser.add_argument('--env', choices=['dev', 'stage', 'prod'],
                             help='Environment label')
    alloc_parser.add_argument('--region', help='Region label')
    
    # List commands
    list_parser = subparsers.add_parser('list', help='List allocations')
    list_parser.add_argument('--vpc', help='Filter by VPC name')
    list_parser.add_argument('--limit', type=int, default=50, 
                            help='Maximum results')
    
    # Delete commands  
    delete_parser = subparsers.add_parser('delete', help='Delete allocation')
    delete_parser.add_argument('allocation_id', type=int, help='Allocation ID')
    
    # Calculate commands
    calc_parser = subparsers.add_parser('calculate', help='Calculate subnet info')
    calc_group = calc_parser.add_mutually_exclusive_group(required=True)
    calc_group.add_argument('--hosts', type=int, help='Number of hosts')
    calc_group.add_argument('--prefix', type=int, help='Prefix length')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    client = IPAMClient(args.url, args.api_key)
    
    try:
        if args.command == 'create-vpc':
            result = client.create_vpc(args.name)
            print(f"✅ VPC '{args.name}' created successfully")
            
        elif args.command == 'allocate':
            result = client.allocate(
                vpc=args.vpc,
                hosts=args.hosts,
                prefix_length=args.prefix,
                environment=args.env,
                region=args.region
            )
            print(f"Allocation created successfully")
            print(f"Primary CIDR: {result['primary_cidr']}")
            print(f"CGNAT CIDR: {result['cgnat_cidr']}")
            print(f"Usable IPs: {result['usable_primary']:,}")
            
        elif args.command == 'list':
            result = client.list_allocations(args.vpc, args.limit)
            allocations = result['items']
            print(f"Found {result['total_count']} allocation(s)")
            print()
            for alloc in allocations:
                print(f"ID: {alloc['allocation_id']} | VPC: {alloc['vpc']}")
                print(f"  Primary: {alloc['primary_cidr']} ({alloc['usable_primary']:,} IPs)")
                print(f"  CGNAT: {alloc['cgnat_cidr']} ({alloc['usable_cgnat']:,} IPs)")
                if alloc.get('labels'):
                    labels = json.loads(alloc['labels']) if isinstance(alloc['labels'], str) else alloc['labels']
                    if labels:
                        print(f"  Labels: {labels}")
                print()
                
        elif args.command == 'delete':
            result = client.delete_allocation(args.allocation_id)
            print(f"Allocation {args.allocation_id} deleted successfully")
            
        elif args.command == 'calculate':
            result = client.calculate(args.hosts, args.prefix)
            print("Subnet Calculation:")
            if result.get('requested_hosts'):
                print(f"  Requested hosts: {result['requested_hosts']}")
            print(f"  Primary subnet: {result['primary_subnet_size']} ({result['usable_primary_ips']:,} usable IPs)")
            print(f"  CGNAT subnet: {result['cgnat_subnet_size']} ({result['usable_cgnat_ips']:,} usable IPs)")
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()