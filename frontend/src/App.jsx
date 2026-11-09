import React, { useState, useEffect } from 'react';
import { 
  Boxes, 
  AlertTriangle, 
  Clock, 
  TrendingUp, 
  ShieldCheck, 
  RefreshCw,
  Store as StoreIcon,
  UploadCloud,
  FileSpreadsheet,
  CheckCircle2,
  XCircle,
  AlertCircle
} from 'lucide-react';

const API_BASE = 'http://localhost:8000/api/v1';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Core selection state
  const [stores, setStores] = useState([]);
  const [selectedStore, setSelectedStore] = useState(null);

  // Dynamic data states
  const [overview, setOverview] = useState(null);
  const [recommendations, setRecommendations] = useState([]);
  const [urgentBatches, setUrgentBatches] = useState([]);
  const [recentMovements, setRecentMovements] = useState([]);

  // Upload Management State
  const [uploadCategory, setUploadCategory] = useState('SALES');
  const [uploadFile, setUploadFile] = useState(null);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadBatches, setUploadBatches] = useState([]);

  // Fetch initial stores
  useEffect(() => {
    fetchStores();
  }, []);

  // Fetch store data on selection change
  useEffect(() => {
    if (selectedStore) {
      loadStoreData(selectedStore.organization_id, selectedStore.id);
      fetchUploadBatches(selectedStore.organization_id);
    }
  }, [selectedStore]);

  const fetchStores = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetch(`${API_BASE}/decision-support/stores`);
      if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch stores`);
      const data = await res.json();
      setStores(data);
      if (data.length > 0) {
        setSelectedStore(data[0]);
      }
    } catch (err) {
      console.warn("Could not load live stores, backend might be starting:", err);
      setError("Cannot reach StockSignal Backend API. Ensure backend is running on http://localhost:8000.");
    } finally {
      setLoading(false);
    }
  };

  const loadStoreData = async (orgId, storeId) => {
    try {
      setLoading(true);
      setError(null);

      // 1. Fetch Overview KPIs
      const overviewRes = await fetch(`${API_BASE}/decision-support/overview?organization_id=${orgId}&store_id=${storeId}`);
      if (overviewRes.ok) {
        const overviewData = await overviewRes.json();
        setOverview(overviewData);
      }

      // 2. Fetch Urgent Perishable Batches
      const perishRes = await fetch(`${API_BASE}/decision-support/perishable/urgent?organization_id=${orgId}&store_id=${storeId}&min_tier=WATCH`);
      if (perishRes.ok) {
        const perishData = await perishRes.json();
        setUrgentBatches(perishData);
      }

      // 3. Fetch Recommendations
      const recsRes = await fetch(`${API_BASE}/decision-support/recommendations?organization_id=${orgId}&store_id=${storeId}`);
      if (recsRes.ok) {
        const recsData = await recsRes.json();
        setRecommendations(recsData);
      }

      // 4. Fetch Ledger History
      const histRes = await fetch(`${API_BASE}/inventory/history?organization_id=${orgId}&store_id=${storeId}&limit=15`);
      if (histRes.ok) {
        const histData = await histRes.json();
        setRecentMovements(histData);
      }

    } catch (err) {
      console.error("Data load error:", err);
      setError("Error synchronizing store operational metrics.");
    } finally {
      setLoading(false);
    }
  };

  const fetchUploadBatches = async (orgId) => {
    try {
      const res = await fetch(`${API_BASE}/data/batches?organization_id=${orgId}`);
      if (res.ok) {
        const data = await res.json();
        setUploadBatches(data);
      }
    } catch (err) {
      console.warn("Could not fetch batches:", err);
    }
  };

  const handleFileUpload = async (e) => {
    e.preventDefault();
    if (!uploadFile || !selectedStore) return;

    try {
      setUploadLoading(true);
      setUploadResult(null);

      const formData = new FormData();
      formData.append('file', uploadFile);
      formData.append('organization_id', selectedStore.organization_id);
      formData.append('category', uploadCategory);
      formData.append('auto_process', 'true');

      const res = await fetch(`${API_BASE}/data/upload`, {
        method: 'POST',
        body: formData,
      });

      const data = await res.json();
      setUploadResult(data);
      setUploadFile(null);

      // Refresh store & batches list
      loadStoreData(selectedStore.organization_id, selectedStore.id);
      fetchUploadBatches(selectedStore.organization_id);

    } catch (err) {
      console.error("Upload error:", err);
      setUploadResult({ status: 'FAILED', error_summary: 'Network error submitting upload.' });
    } finally {
      setUploadLoading(false);
    }
  };

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="logo-icon">SS</div>
          <div>
            <h2 style={{ fontSize: '1.05rem', fontWeight: 700 }}>StockSignal</h2>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>AI Inventory Integrity</p>
          </div>
        </div>

        <nav className="sidebar-nav">
          <div 
            className={`nav-item ${activeTab === 'dashboard' ? 'active' : ''}`}
            onClick={() => setActiveTab('dashboard')}
          >
            <TrendingUp size={18} />
            <span>Overview & Health</span>
          </div>

          <div 
            className={`nav-item ${activeTab === 'uploads' ? 'active' : ''}`}
            onClick={() => setActiveTab('uploads')}
          >
            <UploadCloud size={18} />
            <span>Data Ingestion</span>
          </div>

          <div 
            className={`nav-item ${activeTab === 'ledger' ? 'active' : ''}`}
            onClick={() => setActiveTab('ledger')}
          >
            <Boxes size={18} />
            <span>Double-Entry Ledger</span>
          </div>

          <div 
            className={`nav-item ${activeTab === 'reconciliation' ? 'active' : ''}`}
            onClick={() => setActiveTab('reconciliation')}
          >
            <ShieldCheck size={18} />
            <span>Audit & Discrepancies</span>
          </div>

          <div 
            className={`nav-item ${activeTab === 'perishables' ? 'active' : ''}`}
            onClick={() => setActiveTab('perishables')}
          >
            <Clock size={18} />
            <span>Perishable Expiry (FEFO)</span>
          </div>

          <div 
            className={`nav-item ${activeTab === 'recommendations' ? 'active' : ''}`}
            onClick={() => setActiveTab('recommendations')}
          >
            <AlertTriangle size={18} />
            <span>Action Recommendations</span>
          </div>
        </nav>
      </aside>

      {/* Main Content Area */}
      <main className="main-content">
        <div className="top-bar">
          <div>
            <h1 style={{ fontSize: '1.6rem', fontWeight: 700 }}>Operational Decision Support</h1>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '6px' }}>
              <StoreIcon size={16} color="var(--accent)" />
              <label style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>Active Store:</label>
              {stores.length > 0 ? (
                <select 
                  style={{
                    background: 'var(--bg-secondary)',
                    color: 'var(--text-main)',
                    border: '1px solid var(--border)',
                    padding: '4px 10px',
                    borderRadius: '6px'
                  }}
                  value={selectedStore?.id || ''}
                  onChange={(e) => {
                    const found = stores.find(s => s.id === e.target.value);
                    if (found) setSelectedStore(found);
                  }}
                >
                  {stores.map(s => (
                    <option key={s.id} value={s.id}>{s.name} ({s.store_code})</option>
                  ))}
                </select>
              ) : (
                <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                  {loading ? 'Discovering stores...' : 'No stores registered.'}
                </span>
              )}
            </div>
          </div>
          <button 
            className="btn" 
            onClick={() => selectedStore && loadStoreData(selectedStore.organization_id, selectedStore.id)}
            disabled={loading}
            style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
          >
            <RefreshCw size={16} className={loading ? 'spin' : ''} />
            {loading ? 'Synchronizing...' : 'Refresh Live Data'}
          </button>
        </div>

        {error && (
          <div style={{ padding: '12px 16px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.4)', borderRadius: '8px', marginBottom: '24px', color: '#fca5a5' }}>
            {error}
          </div>
        )}

        {/* Global Key Stats — 100% Data-Driven */}
        <div className="stats-grid">
          <div className="stat-card">
            <div className="stat-label">Active Monitored SKUs</div>
            <div className="stat-value">{overview ? overview.active_skus : (loading ? '...' : 0)}</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '4px' }}>
              {overview && overview.active_skus > 0 ? 'Live in catalog' : 'No store products'}
            </div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Perishable Batches on Watch</div>
            <div className="stat-value" style={{ color: urgentBatches.length > 0 ? 'var(--warning)' : 'var(--text-main)' }}>
              {urgentBatches.length}
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '4px' }}>
              {urgentBatches.length > 0 ? 'FEFO action advised' : 'All rotations safe'}
            </div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Estimated Value at Risk</div>
            <div className="stat-value" style={{ color: overview?.total_value_at_risk > 0 ? 'var(--danger)' : 'var(--text-main)' }}>
              ₹{overview ? Number(overview.total_value_at_risk).toLocaleString() : '0'}
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '4px' }}>
              Potential expiry loss
            </div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Pending Action Interventions</div>
            <div className="stat-value">{recommendations.length}</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--accent)', marginTop: '4px' }}>
              {recommendations.length > 0 ? 'Prioritized actions' : 'Zero flags'}
            </div>
          </div>
        </div>

        {/* Dashboard Overview */}
        {activeTab === 'dashboard' && (
          <>
            <div className="card">
              <div className="card-title">
                <span>Active Algorithmic Recommendations</span>
                <span className="badge badge-watch">{recommendations.length} Found</span>
              </div>
              {recommendations.length > 0 ? (
                <table>
                  <thead>
                    <tr>
                      <th>Action Type</th>
                      <th>Priority</th>
                      <th>SKU</th>
                      <th>Operational Reasoning</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recommendations.slice(0, 5).map((r, idx) => (
                      <tr key={r.recommendation_id || idx}>
                        <td><strong>{r.action_type}</strong></td>
                        <td>
                          <span className={`badge badge-${r.priority.toLowerCase()}`}>
                            {r.priority}
                          </span>
                        </td>
                        <td><code>{r.sku || 'N/A'}</code></td>
                        <td>{r.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p style={{ color: 'var(--text-muted)', padding: '16px 0' }}>
                  No active recommendations. Inventory levels and rotations are currently aligned.
                </p>
              )}
            </div>

            <div className="card">
              <div className="card-title">
                <span>Recent Ledger Movements</span>
                <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Verified Double-Entry Postings</span>
              </div>
              {recentMovements.length > 0 ? (
                <table>
                  <thead>
                    <tr>
                      <th>Occurred At</th>
                      <th>Movement Type</th>
                      <th>Quantity Delta</th>
                      <th>Source System</th>
                      <th>Idempotency Key</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentMovements.map(m => (
                      <tr key={m.id}>
                        <td>{new Date(m.occurred_at).toLocaleString()}</td>
                        <td><strong>{m.movement_type}</strong></td>
                        <td style={{ color: Number(m.quantity_delta) >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                          {m.quantity_delta}
                        </td>
                        <td>{m.source_system}</td>
                        <td><small style={{ color: 'var(--text-muted)' }}>{m.idempotency_key?.slice(0, 12)}...</small></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p style={{ color: 'var(--text-muted)', padding: '16px 0' }}>
                  No ledger transactions recorded for this store yet.
                </p>
              )}
            </div>
          </>
        )}

        {/* Data Ingestion & Management Screen */}
        {activeTab === 'uploads' && (
          <>
            <div className="card">
              <div className="card-title">
                <span>Retail Dataset Ingestion Wizard</span>
                <span className="badge badge-watch">CSV / Excel Parser</span>
              </div>
              <p style={{ color: 'var(--text-muted)', marginBottom: '20px' }}>
                Upload POS sales logs, inventory snapshots, or Goods Received Notes (GRN). Data is validated for schema fidelity, duplicate-checked via SHA-256 hash, and ingested atomically into the double-entry ledger.
              </p>

              <form onSubmit={handleFileUpload} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: '16px', alignItems: 'end' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '6px' }}>Dataset Category</label>
                  <select 
                    value={uploadCategory} 
                    onChange={e => setUploadCategory(e.target.value)}
                    style={{ width: '100%', background: 'var(--bg-secondary)', color: 'var(--text-main)', border: '1px solid var(--border)', padding: '8px 12px', borderRadius: '6px' }}
                  >
                    <option value="SALES">Daily POS Sales (sku, store_code, quantity, occurred_at)</option>
                    <option value="INVENTORY_SNAPSHOT">Inventory Snapshot (sku, store_code, counted_qty)</option>
                    <option value="GOODS_RECEIVED">Goods Received Notes (sku, store_code, quantity, invoice_number)</option>
                    <option value="PHYSICAL_COUNT">Physical Audit Count (sku, store_code, bucket, quantity)</option>
                  </select>
                </div>

                <div>
                  <label style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '6px' }}>Select File</label>
                  <input 
                    type="file" 
                    accept=".csv"
                    onChange={e => setUploadFile(e.target.files[0])}
                    style={{ width: '100%', background: 'var(--bg-secondary)', color: 'var(--text-main)', border: '1px solid var(--border)', padding: '6px 12px', borderRadius: '6px' }}
                  />
                </div>

                <button 
                  type="submit" 
                  className="btn" 
                  disabled={uploadLoading || !uploadFile}
                  style={{ display: 'flex', alignItems: 'center', gap: '8px', height: '40px' }}
                >
                  <UploadCloud size={16} />
                  {uploadLoading ? 'Ingesting...' : 'Import Dataset'}
                </button>
              </form>

              {uploadResult && (
                <div style={{ marginTop: '20px', padding: '16px', borderRadius: '8px', background: uploadResult.status === 'COMPLETED' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)', border: '1px solid ' + (uploadResult.status === 'COMPLETED' ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)') }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontWeight: 600 }}>
                    {uploadResult.status === 'COMPLETED' ? <CheckCircle2 size={18} color="var(--success)" /> : <AlertCircle size={18} color="var(--danger)" />}
                    <span>Status: {uploadResult.status}</span>
                  </div>
                  <p style={{ fontSize: '0.85rem', marginTop: '6px' }}>
                    File: <strong>{uploadResult.file_name}</strong> | Total: {uploadResult.total_rows} | Valid: {uploadResult.valid_rows} | Rejected: {uploadResult.rejected_rows}
                  </p>
                  {uploadResult.error_summary && (
                    <p style={{ color: '#fca5a5', fontSize: '0.85rem', marginTop: '4px' }}>{uploadResult.error_summary}</p>
                  )}
                </div>
              )}
            </div>

            <div className="card">
              <div className="card-title">
                <span>Dataset Ingestion Audit Log</span>
                <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Historical Data Lineage</span>
              </div>
              {uploadBatches.length > 0 ? (
                <table>
                  <thead>
                    <tr>
                      <th>Upload Date</th>
                      <th>Category</th>
                      <th>File Name</th>
                      <th>Status</th>
                      <th>Total Rows</th>
                      <th>Valid Rows</th>
                      <th>Rejections</th>
                    </tr>
                  </thead>
                  <tbody>
                    {uploadBatches.map(b => (
                      <tr key={b.id}>
                        <td>{new Date(b.created_at).toLocaleString()}</td>
                        <td><strong>{b.category}</strong></td>
                        <td>{b.file_name}</td>
                        <td>
                          <span className={`badge badge-${b.status.toLowerCase().replace('_', '-')}`}>
                            {b.status}
                          </span>
                        </td>
                        <td>{b.total_rows}</td>
                        <td style={{ color: 'var(--success)' }}>{b.valid_rows}</td>
                        <td style={{ color: b.rejected_rows > 0 ? 'var(--danger)' : 'var(--text-muted)' }}>{b.rejected_rows}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p style={{ color: 'var(--text-muted)', padding: '16px 0' }}>No datasets uploaded yet.</p>
              )}
            </div>
          </>
        )}

        {/* Ledger View */}
        {activeTab === 'ledger' && (
          <div className="card">
            <div className="card-title">
              <span>Audit Trail (Movements & Balance Constraints)</span>
            </div>
            {recentMovements.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>Timestamp</th>
                    <th>Type</th>
                    <th>Total Delta</th>
                    <th>Document Reference</th>
                    <th>Integrity Verification</th>
                  </tr>
                </thead>
                <tbody>
                  {recentMovements.map(m => (
                    <tr key={m.id}>
                      <td>{new Date(m.occurred_at).toLocaleString()}</td>
                      <td><strong>{m.movement_type}</strong></td>
                      <td>{m.quantity_delta}</td>
                      <td>{m.external_document_id || 'N/A'}</td>
                      <td><span className="badge badge-safe">POSTED & BALANCED</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p style={{ color: 'var(--text-muted)', padding: '16px 0' }}>No ledger entries found.</p>
            )}
          </div>
        )}

        {/* Perishables View */}
        {activeTab === 'perishables' && (
          <div className="card">
            <div className="card-title">
              <span>Perishable Batch Shelf-Life Monitor (FEFO Ranking)</span>
            </div>
            {urgentBatches.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>Batch Code</th>
                    <th>Days Remaining</th>
                    <th>Risk Tier</th>
                    <th>Current Sellable Qty</th>
                    <th>Expected Demand</th>
                    <th>Est. Unsold Qty</th>
                    <th>Value at Risk</th>
                  </tr>
                </thead>
                <tbody>
                  {urgentBatches.map(b => (
                    <tr key={b.batch_id}>
                      <td><strong>{b.batch_code || b.batch_id.slice(0, 8)}</strong></td>
                      <td>{b.days_remaining !== null ? `${b.days_remaining} days` : 'No Expiry'}</td>
                      <td>
                        <span className={`badge badge-${b.risk_tier.toLowerCase()}`}>
                          {b.risk_tier}
                        </span>
                      </td>
                      <td>{b.current_qty}</td>
                      <td>{b.expected_demand_before_expiry}</td>
                      <td style={{ color: Number(b.estimated_unsold_qty) > 0 ? 'var(--danger)' : 'var(--text-main)' }}>
                        {b.estimated_unsold_qty}
                      </td>
                      <td>₹{Number(b.value_at_risk).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p style={{ color: 'var(--text-muted)', padding: '16px 0' }}>
                No batches on urgent watch. Stock rotation is within safe thresholds.
              </p>
            )}
          </div>
        )}

        {/* Audit & Discrepancies View */}
        {activeTab === 'reconciliation' && (
          <div className="card">
            <div className="card-title">
              <span>Physical Stock Audit Reconciliation</span>
            </div>
            <p style={{ color: 'var(--text-muted)', marginBottom: '16px' }}>
              Physical floor counts are reconciled against double-entry ledger balances at transaction commit.
            </p>
            <div style={{ padding: '24px', background: 'var(--bg-secondary)', borderRadius: '8px', border: '1px dashed var(--border)', textAlign: 'center' }}>
              <ShieldCheck size={36} color="var(--accent)" style={{ margin: '0 auto 12px auto' }} />
              <h3 style={{ fontSize: '1.05rem', marginBottom: '6px' }}>Ready for Stock Count Session</h3>
              <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', maxWidth: '450px', margin: '0 auto' }}>
                Physical counts submitted through the auditor interface will automatically compute discrepancies, identify shrinkage vs. misclassifications, and generate suggested corrections.
              </p>
            </div>
          </div>
        )}

        {/* Recommendations View */}
        {activeTab === 'recommendations' && (
          <div className="card">
            <div className="card-title">
              <span>Autonomous Decision-Support Recommendations</span>
            </div>
            {recommendations.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {recommendations.map(r => (
                  <div key={r.recommendation_id} style={{ padding: '16px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: '8px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                      <span className={`badge badge-${r.priority.toLowerCase()}`}>{r.priority}</span>
                      <strong style={{ color: 'var(--accent)' }}>{r.action_type}</strong>
                    </div>
                    <p style={{ fontSize: '0.9rem', marginBottom: '4px' }}>{r.reason}</p>
                    {r.sku && <small style={{ color: 'var(--text-muted)' }}>Target SKU: <code>{r.sku}</code></small>}
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: 'var(--text-muted)', padding: '16px 0' }}>
                No interventions required. All inventory indicators are healthy.
              </p>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
