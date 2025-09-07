let allocations = [];
let allocationMode = 'hosts';

function setStatus(message, type = 'success') {
    const status = document.getElementById('status');
    status.textContent = message;
    status.className = `status ${type}`;
}

function showLoading(show = true) {
    const refreshText = document.getElementById('refresh-text');
    const spinner = document.getElementById('loading-spinner');
    
    if (show) {
        refreshText.style.display = 'none';
        spinner.style.display = 'inline-block';
        setStatus('Loading...', 'loading');
    } else {
        refreshText.style.display = 'inline';
        spinner.style.display = 'none';
    }
}

function showCreateLoading(show = true) {
    const createText = document.getElementById('create-text');
    const createSpinner = document.getElementById('create-spinner');
    
    if (show) {
        createText.style.display = 'none';
        createSpinner.style.display = 'inline-block';
    } else {
        createText.style.display = 'inline';
        createSpinner.style.display = 'none';
    }
}

function toggleAllocationForm() {
    const form = document.getElementById('allocation-form');
    form.classList.toggle('show');
    
    if (form.classList.contains('show')) {
        document.getElementById('vpc-name').focus();
    } else {
        document.getElementById('vpc-name').value = '';
        document.getElementById('host-count').value = '';
        document.getElementById('prefix-length').value = '';
        setAllocationMode('hosts');
    }
}

function setAllocationMode(mode) {
    allocationMode = mode;
    
    const hostsMode = document.getElementById('hosts-mode');
    const prefixMode = document.getElementById('prefix-mode');
    const hostsInput = document.getElementById('hosts-input');
    const prefixInput = document.getElementById('prefix-input');
    
    if (mode === 'hosts') {
        hostsMode.classList.add('active');
        prefixMode.classList.remove('active');
        hostsInput.style.display = 'flex';
        prefixInput.style.display = 'none';
    } else {
        prefixMode.classList.add('active');
        hostsMode.classList.remove('active');
        prefixInput.style.display = 'flex';
        hostsInput.style.display = 'none';
    }
}

async function createAllocation() {
    const vpcName = document.getElementById('vpc-name').value.trim();
    const hostCount = document.getElementById('host-count').value;
    const prefixLength = document.getElementById('prefix-length').value;
    
    if (!vpcName) {
        setStatus('VPC name is required', 'error');
        document.getElementById('vpc-name').focus();
        return;
    }
    
    if (allocationMode === 'hosts') {
        if (!hostCount || hostCount < 1 || hostCount > 4000) {
            setStatus('Host count must be between 1 and 4000', 'error');
            document.getElementById('host-count').focus();
            return;
        }
    } else {
        if (!prefixLength) {
            setStatus('Please select a prefix length', 'error');
            document.getElementById('prefix-length').focus();
            return;
        }
    }
    
    showCreateLoading(true);
    setStatus('Creating allocation...', 'loading');
    
    try {
        const payload = { vpc: vpcName };
        
        if (allocationMode === 'hosts') {
            payload.hosts = parseInt(hostCount);
        } else {
            payload.prefix_length = parseInt(prefixLength);
        }
        
        const response = await api('/allocate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `HTTP ${response.status}`);
        }
        
        const result = await response.json();
        
        setStatus(`Allocation created successfully!`, 'success');
        
        toggleAllocationForm();
        await loadAllocations();
        
        setTimeout(() => {
            setStatus(`Allocated ${result.primary_cidr} (${result.usable_primary.toLocaleString()} usable IPs)`, 'success');
        }, 1000);
        
    } catch (error) {
        console.error('Error creating allocation:', error);
        setStatus(`Error: ${error.message}`, 'error');
    } finally {
        showCreateLoading(false);
    }
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && document.getElementById('allocation-form').classList.contains('show')) {
        e.preventDefault();
        createAllocation();
    }
    if (e.key === 'Escape') {
        const form = document.getElementById('allocation-form');
        if (form.classList.contains('show')) {
            toggleAllocationForm();
        }
    }
});

async function loadAllocations() {
    showLoading(true);
    
    try {
        const response = await api('/allocations');
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        allocations = Array.isArray(data) ? data : (data.items ?? []);
        const total = Array.isArray(data) ? data.length : (data.total_count ?? allocations.length);
        renderAllocations();
        renderSummary();
        setStatus(`Loaded ${allocations.length} allocation${allocations.length !== 1 ? 's' : ''}`);
        
    } catch (error) {
        console.error('Error loading allocations:', error);
        setStatus(`Error: ${error.message}`, 'error');
        document.getElementById('allocations-container').innerHTML = `
            <div class="empty-state">
                <h3>Connection Error</h3>
                <p>Could not connect to the IPAM API</p>
                <p><small>${error.message}</small></p>
            </div>
        `;
    } finally {
        showLoading(false);
    }
}

function renderSummary() {
    const summaryContainer = document.getElementById('summary-stats');
    
    if (allocations.length === 0) {
        summaryContainer.style.display = 'none';
        return;
    }
    
    const vpcCount = new Set(allocations.map(a => a.vpc)).size;
    const totalPrimaryIPs = allocations.reduce((sum, a) => sum + a.usable_primary, 0);
    const totalCgnatIPs = allocations.reduce((sum, a) => sum + a.usable_cgnat, 0);
    
    summaryContainer.innerHTML = `
        <div class="stat-card">
            <div class="stat-value">${vpcCount}</div>
            <div class="stat-label">VPCs</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${allocations.length}</div>
            <div class="stat-label">Allocations</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${totalPrimaryIPs.toLocaleString()}</div>
            <div class="stat-label">Primary IPs</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${totalCgnatIPs.toLocaleString()}</div>
            <div class="stat-label">CGNAT IPs</div>
        </div>
    `;
    
    summaryContainer.style.display = 'grid';
}

function renderAllocations() {
    const container = document.getElementById('allocations-container');
    
    if (allocations.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h3>No Allocations Found</h3>
                <p>Create your first VPC and allocation using the API</p>
                <p><small>Try: <code>{"vpc":"test","hosts":100}</code></small></p>
                <button class="btn" onclick="window.open('/docs', '_blank')">Open API Docs</button>
            </div>
        `;
        return;
    }
    
    const allocationsHtml = allocations.map(allocation => {
        let metaInfo = '';
        if (allocation.requested_hosts) {
            metaInfo = `Requested: ${allocation.requested_hosts} hosts`;
        } else if (allocation.requested_prefix) {
            metaInfo = `Requested: /${allocation.requested_prefix}`;
        }
        
        return `
            <div class="allocation-card">
                <div class="card-actions">
                    <div class="card-action-btn edit" onclick="editAllocationVpc(${allocation.allocation_id}, '${allocation.vpc}')" title="Move to Different VPC">Edit</div>
                    <div class="card-action-btn delete" onclick="deleteAllocation(${allocation.allocation_id}, '${allocation.primary_cidr}')" title="Delete This Allocation">Delete</div>
                </div>
                <div class="card-header">
                    <div>
                        <h3 class="vpc-name">${allocation.vpc}</h3>
                        ${metaInfo ? `<div class="allocation-meta">${metaInfo}</div>` : ''}
                    </div>
                    <span>VPC</span>
                </div>
                <div class="card-body">
                    <div class="cidr-section primary-cidr">
                        <div class="cidr-label">Primary CIDR</div>
                        <div class="cidr-value">${allocation.primary_cidr}</div>
                        <div class="usable-ips">
                            <span>Usable IPs:</span>
                            <span class="ip-count">${allocation.usable_primary.toLocaleString()}</span>
                        </div>
                    </div>
                    
                    <div class="cidr-section cgnat-cidr">
                        <div class="cidr-label">CGNAT CIDR</div>
                        <div class="cidr-value">${allocation.cgnat_cidr}</div>
                        <div class="usable-ips">
                            <span>Usable IPs:</span>
                            <span class="ip-count">${allocation.usable_cgnat.toLocaleString()}</span>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
    
    container.innerHTML = `<div class="allocation-grid">${allocationsHtml}</div>`;
}

async function editAllocationVpc(allocationId, currentVpc) {
    const newVpcName = prompt(`Move this allocation to which VPC?`, currentVpc);
    
    if (!newVpcName || newVpcName.trim() === '' || newVpcName.trim() === currentVpc) {
        return;
    }
    
    setStatus('Moving allocation to new VPC...', 'loading');
    
    try {
        const response = await api(`/allocations/${allocationId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_vpc_name: newVpcName.trim() })
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to move allocation');
        }
        
        setStatus(`Allocation moved to VPC "${newVpcName.trim()}"`, 'success');
        await loadAllocations();
        
    } catch (error) {
        console.error('Error moving allocation:', error);
        setStatus(`Error: ${error.message}`, 'error');
    }
}

async function deleteAllocation(allocationId, primaryCidr) {
    if (!confirm(`Are you sure you want to delete allocation "${primaryCidr}"?\n\nThis action cannot be undone.`)) {
        return;
    }
    
    setStatus('Deleting allocation...', 'loading');
    
    try {
        const response = await api(`/allocations/${allocationId}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to delete allocation');
        }
        
        setStatus(`Allocation "${primaryCidr}" deleted`, 'success');
        await loadAllocations();
        
    } catch (error) {
        console.error('Error deleting allocation:', error);
        setStatus(`Error: ${error.message}`, 'error');
    }
}

async function deleteVpc(vpcName) {
    const vpcAllocations = allocations.filter(a => a.vpc === vpcName);
    
    if (!confirm(`Are you sure you want to delete VPC "${vpcName}"?\n\nThis will delete ${vpcAllocations.length} allocation(s) for this VPC. This action cannot be undone.`)) {
        return;
    }
    
    setStatus('Deleting VPC...', 'loading');
    
    try {
        const response = await api(`/vpcs/${encodeURIComponent(vpcName)}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to delete VPC');
        }
        
        setStatus(`VPC "${vpcName}" and ${vpcAllocations.length} allocation(s) deleted`, 'success');
        await loadAllocations();
        
    } catch (error) {
        console.error('Error deleting VPC:', error);
        setStatus(`Error: ${error.message}`, 'error');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadAllocations();
});

