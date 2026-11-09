import React, { useState } from 'react';
import { 
  Boxes, 
  AlertTriangle, 
  CheckCircle2, 
  Clock, 
  TrendingUp, 
  Layers, 
  ArrowRightLeft,
  ShieldCheck,
  RefreshCw
} from 'lucide-react';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="logo-icon">SS</div>
          <div>
            <h2 style={{ fontSize: '1.05rem', fontWeight: 700 }}>StockSignal</h2>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Enterprise Retail Core</p>
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
            <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
              Store: <strong>Indiranagar (STORE001)</strong> | Organization: <strong>Demo Supermarket Corp</strong>
            </p>
          </div>
          <button className="btn" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <RefreshCw size={16} /> Sync Live Data
          </button>
        </div>

        {/* Global Key Stats */}
        <div className="stats-grid">
          <div className="stat-card">
            <div className="stat-label">Active SKUs</div>
            <div className="stat-value">24</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--success)', marginTop: '4px' }}>100% Tracked</div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Ledger Discrepancy Gap</div>
            <div className="stat-value" style={{ color: 'var(--warning)' }}>-20.000</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--warning)', marginTop: '4px' }}>1 Shrinkage Event Flagged</div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Perishables At Risk</div>
            <div className="stat-value" style={{ color: 'var(--danger)' }}>₹1,800.00</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--danger)', marginTop: '4px' }}>Batch B2 Expires in 2 days</div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Pending Interventions</div>
            <div className="stat-value">2</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--accent)', marginTop: '4px' }}>Physical Count & FEFO</div>
          </div>
        </div>

        {/* Dashboard Overview */}
        {activeTab === 'dashboard' && (
          <>
            <div className="card">
              <div className="card-title">
                <span>Critical Action Recommendations</span>
                <span className="badge badge-critical">Urgent Review</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>SKU / Product</th>
                    <th>Action Required</th>
                    <th>Priority</th>
                    <th>Root Cause / Reasoning</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><strong>AMUL_MILK_500ML</strong></td>
                    <td>MARKDOWN_REVIEW</td>
                    <td><span className="badge badge-critical">CRITICAL</span></td>
                    <td>Batch B2 expires in 2 days (18 units projected unsold). Value at risk: ₹1,800.</td>
                    <td><span className="badge badge-watch">PENDING</span></td>
                  </tr>
                  <tr>
                    <td><strong>FORTUNE_SUNFLOWER_1L</strong></td>
                    <td>PHYSICAL_COUNT</td>
                    <td><span className="badge badge-high">HIGH</span></td>
                    <td>High-severity shrink gap of -20 units detected vs. physical count.</td>
                    <td><span className="badge badge-watch">PENDING</span></td>
                  </tr>
                  <tr>
                    <td><strong>AASHIRVAAD_ATTA_5KG</strong></td>
                    <td>REPLENISH</td>
                    <td><span className="badge badge-medium">MEDIUM</span></td>
                    <td>Current stock (12.0) has fallen below reorder threshold (20.0).</td>
                    <td><span className="badge badge-ok">APPROVED</span></td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="card">
              <div className="card-title">
                <span>Recent Ledger Movements</span>
                <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Immutable Audit Trail</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Timestamp</th>
                    <th>Type</th>
                    <th>SKU</th>
                    <th>Quantity Delta</th>
                    <th>Bucket Allocation</th>
                    <th>Invariant Check</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Just now</td>
                    <td>RECLASSIFY</td>
                    <td>AMUL_BUTTER_100G</td>
                    <td>0.000</td>
                    <td>SELLABLE: -5.000 / QUARANTINED: +5.000</td>
                    <td><span className="badge badge-safe">VERIFIED</span></td>
                  </tr>
                  <tr>
                    <td>10m ago</td>
                    <td>SALE</td>
                    <td>FORTUNE_SUNFLOWER_1L</td>
                    <td>-2.000</td>
                    <td>SELLABLE: -2.000</td>
                    <td><span className="badge badge-safe">VERIFIED</span></td>
                  </tr>
                  <tr>
                    <td>1h ago</td>
                    <td>GRN_RECEIPT</td>
                    <td>AASHIRVAAD_ATTA_5KG</td>
                    <td>+50.000</td>
                    <td>SELLABLE: +50.000</td>
                    <td><span className="badge badge-safe">VERIFIED</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </>
        )}

        {/* Ledger View */}
        {activeTab === 'ledger' && (
          <div className="card">
            <div className="card-title">
              <span>Inventory Ledger & Bucket Partitions</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Product SKU</th>
                  <th>Sellable Quantity</th>
                  <th>Quarantined Quantity</th>
                  <th>Total Physical On-Hand</th>
                  <th>Ledger Integrity</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>AMUL_MILK_500ML</td>
                  <td>120.000</td>
                  <td>0.000</td>
                  <td>120.000</td>
                  <td><span className="badge badge-safe">BALANCED</span></td>
                </tr>
                <tr>
                  <td>FORTUNE_SUNFLOWER_1L</td>
                  <td>80.000</td>
                  <td>0.000</td>
                  <td>80.000</td>
                  <td><span className="badge badge-safe">BALANCED</span></td>
                </tr>
                <tr>
                  <td>AMUL_BUTTER_100G</td>
                  <td>45.000</td>
                  <td>5.000</td>
                  <td>50.000</td>
                  <td><span className="badge badge-safe">BALANCED</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        )}

        {/* Perishables View */}
        {activeTab === 'perishables' && (
          <div className="card">
            <div className="card-title">
              <span>Batch Shelf-Life Risk Assessment (FEFO Rotation)</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Batch Code</th>
                  <th>Product SKU</th>
                  <th>Days Remaining</th>
                  <th>Sellable Qty</th>
                  <th>Exp. Demand Before Expiry</th>
                  <th>Estimated Unsold Qty</th>
                  <th>FEFO Priority</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>B2-20261111</strong></td>
                  <td>AMUL_MILK_500ML</td>
                  <td><span style={{ color: 'var(--danger)', fontWeight: 700 }}>2 days</span></td>
                  <td>20.000</td>
                  <td>2.000</td>
                  <td>18.000</td>
                  <td><span className="badge badge-critical">1 (DISPATCH FIRST)</span></td>
                </tr>
                <tr>
                  <td><strong>B1-20261120</strong></td>
                  <td>AMUL_MILK_500ML</td>
                  <td>11 days</td>
                  <td>100.000</td>
                  <td>11.000</td>
                  <td>89.000</td>
                  <td><span className="badge badge-watch">2 (ROTATION SAFE)</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        )}

        {/* Audit / Reconciliation View */}
        {activeTab === 'reconciliation' && (
          <div className="card">
            <div className="card-title">
              <span>Audit Session Reconciliation</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Product SKU</th>
                  <th>Ledger Physical</th>
                  <th>Physical Audit Count</th>
                  <th>Net Discrepancy</th>
                  <th>Classification</th>
                  <th>Severity Tier</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>FORTUNE_SUNFLOWER_1L</td>
                  <td>100.000</td>
                  <td>80.000</td>
                  <td><strong style={{ color: 'var(--danger)' }}>-20.000</strong></td>
                  <td>SHRINKAGE / UNRECORDED LOSS</td>
                  <td><span className="badge badge-critical">HIGH</span></td>
                </tr>
                <tr>
                  <td>AMUL_MILK_500ML</td>
                  <td>120.000</td>
                  <td>120.000</td>
                  <td>0.000</td>
                  <td>EXACT MATCH</td>
                  <td><span className="badge badge-safe">OK</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        )}

        {/* Recommendations View */}
        {activeTab === 'recommendations' && (
          <div className="card">
            <div className="card-title">
              <span>Active Decision Support Feed</span>
            </div>
            <p style={{ color: 'var(--text-muted)', marginBottom: '16px' }}>
              Autonomous recommendations synthesized from cross-bucket movements, physical audits, and expiry deadlines.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ padding: '16px', background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.3)', borderRadius: '8px' }}>
                <h4 style={{ color: '#f87171', marginBottom: '6px' }}>Immediate Action: Markdown Review for AMUL_MILK_500ML</h4>
                <p style={{ fontSize: '0.9rem' }}>Batch B2 has only 2 days remaining. Markdown by 30% or allocate to bulk orders immediately to prevent ₹1,800 write-off.</p>
              </div>
              <div style={{ padding: '16px', background: 'rgba(245, 158, 11, 0.1)', border: '1px solid rgba(245, 158, 11, 0.3)', borderRadius: '8px' }}>
                <h4 style={{ color: '#fbbf24', marginBottom: '6px' }}>Verification Required: Physical Count on FORTUNE_SUNFLOWER_1L</h4>
                <p style={{ fontSize: '0.9rem' }}>High inventory shrink gap of 20 units detected between floor count and ledger. Dispatch auditor for verification.</p>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
