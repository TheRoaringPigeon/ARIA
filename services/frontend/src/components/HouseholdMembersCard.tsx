import { useState } from 'react'
import { useCreateInvite, useInvites, useMembers, useRevokeInvite } from '../hooks/useHousehold'
import { useSession } from '../hooks/useSession'

export function HouseholdMembersCard() {
  const { data: session } = useSession()
  const { data: members } = useMembers()
  const isOwner = session?.role === 'owner'

  return (
    <div className="rounded-lg border border-divider p-6">
      <h2 className="text-sm font-semibold mb-3">Household members</h2>
      <ul className="flex flex-col gap-2">
        {members?.map((member) => (
          <li key={member.id} className="flex items-center justify-between text-sm">
            <span>
              {member.name} <span className="text-subtle">({member.email})</span>
            </span>
            <span className="text-xs text-subtle">{member.role}</span>
          </li>
        ))}
      </ul>

      {isOwner && <InviteSection />}
    </div>
  )
}

function InviteSection() {
  const { data: invites } = useInvites()
  const createInvite = useCreateInvite()
  const revokeInvite = useRevokeInvite()
  const [copiedToken, setCopiedToken] = useState<string | null>(null)

  function inviteUrl(token: string): string {
    return `${window.location.origin}/invite/${token}`
  }

  async function copyLink(token: string) {
    await navigator.clipboard.writeText(inviteUrl(token))
    setCopiedToken(token)
    setTimeout(() => setCopiedToken((current) => (current === token ? null : current)), 2000)
  }

  return (
    <div className="mt-4 border-t border-divider pt-4">
      <button
        type="button"
        onClick={() => createInvite.mutate()}
        disabled={createInvite.isPending}
        className="rounded-md border border-line px-3 py-1.5 text-sm font-medium disabled:opacity-50"
      >
        {createInvite.isPending ? 'Creating…' : 'Invite a member'}
      </button>

      {invites && invites.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          {invites.map((invite) => (
            <div key={invite.token} className="flex items-center gap-2">
              <input
                readOnly
                value={inviteUrl(invite.token)}
                className="flex-1 rounded-md border border-line bg-transparent px-2 py-1 text-xs"
              />
              <button
                type="button"
                onClick={() => copyLink(invite.token)}
                className="text-sm text-subtle hover:underline shrink-0"
              >
                {copiedToken === invite.token ? 'Copied!' : 'Copy'}
              </button>
              <button
                type="button"
                onClick={() => revokeInvite.mutate(invite.token)}
                className="text-sm text-red-500 hover:underline shrink-0"
              >
                Revoke
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
