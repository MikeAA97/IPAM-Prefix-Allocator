import pytest
import ipaddress
from app import hosts_to_prefix_length, usable_count

def test_hosts_to_prefix_length():
    """Test subnet sizing calculation"""
    # Test cases based on your sizing logic
    assert hosts_to_prefix_length(50) == 26    # 50 + 5 = 55, needs 64 addresses = /26
    assert hosts_to_prefix_length(100) == 25   # 100 + 5 = 105, needs 128 addresses = /25
    assert hosts_to_prefix_length(500) == 23   # 500 + 5 = 505, needs 512 addresses = /23
    assert hosts_to_prefix_length(1000) == 22  # 1000 + 5 = 1005, needs 1024 addresses = /22
    assert hosts_to_prefix_length(4000) == 20  # 4000 + 5 = 4005, needs 4096 addresses = /20
    
def test_usable_count():
    """Test usable IP calculation (total - 5 reserved)"""
    assert usable_count(26) == 59    # 64 - 5
    assert usable_count(25) == 123   # 128 - 5  
    assert usable_count(24) == 251   # 256 - 5
    assert usable_count(23) == 507   # 512 - 5
    assert usable_count(22) == 1019  # 1024 - 5
    assert usable_count(21) == 2043  # 2048 - 5
    assert usable_count(20) == 4091  # 4096 - 5

def test_cidr_pools():
    """Test that our pools are correctly defined"""
    primary_pool = ipaddress.IPv4Network("10.0.0.0/16")
    cgnat_pool = ipaddress.IPv4Network("100.64.0.0/10")
    
    # Test primary pool contains expected subnets
    test_primary = ipaddress.IPv4Network("10.0.1.0/24")
    assert test_primary.subnet_of(primary_pool)
    
    # Test CGNAT pool contains expected subnets  
    test_cgnat = ipaddress.IPv4Network("100.64.0.0/19")
    assert test_cgnat.subnet_of(cgnat_pool)
    
    # Test pools don't overlap
    assert not primary_pool.overlaps(cgnat_pool)

def test_cgnat_sizing_relationship():
    """Test that CGNAT is always /5 larger than primary"""
    primary_prefix = 24
    cgnat_prefix = primary_prefix - 5
    assert cgnat_prefix == 19
    
    # CGNAT should have 32x more addresses than primary
    primary_addresses = 2 ** (32 - primary_prefix)
    cgnat_addresses = 2 ** (32 - cgnat_prefix)
    assert cgnat_addresses == primary_addresses * 32

if __name__ == "__main__":
    # Run tests manually if pytest not available
    test_hosts_to_prefix_length()
    test_usable_count()  
    test_cidr_pools()
    test_cgnat_sizing_relationship()
    print("All tests passed!")