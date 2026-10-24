export function Message({ error, success }) {
  if (error) return <div className="message error" role="alert">{error}</div>;
  if (success) return <div className="message success" role="status">{success}</div>;
  return null;
}
