export default function PresenceBar({ users = [] }) {
  return (
    <div className="presence-bar">
      {users.map((user) => (
        <div className="avatar" key={user.user_id} title={`${user.username} · ${user.role}`} style={{ backgroundColor: user.color }}>
          {(user.username || '?').slice(0, 1).toUpperCase()}
        </div>
      ))}
    </div>
  )
}
