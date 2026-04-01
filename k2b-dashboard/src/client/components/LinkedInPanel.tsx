export function LinkedInPanel() {
  return (
    <div className="panel">
      <span className="panel-title">LinkedIn Performance</span>
      <div className="linkedin-placeholder">
        <div className="text-muted" style={{ fontSize: 12, textAlign: "center", padding: "16px 0" }}>
          Connect LinkedIn API to see metrics
          <div style={{ fontSize: 11, marginTop: 6 }}>
            Post engagement data will appear here once
            <br />
            <code style={{ color: "var(--blue)", fontSize: 10 }}>linkedin-metrics.json</code> is populated
          </div>
        </div>
      </div>
    </div>
  );
}
