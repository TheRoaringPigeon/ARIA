import { useState, type FormEvent } from 'react'
import type { EntityCreateInput } from '../api/entities'
import {
  STATUS_BY_DOMAIN,
  type Entity,
  type EntityAttributes,
  type EntityDomain,
} from '../api/types'

const DOMAINS: EntityDomain[] = ['home', 'vehicle', 'equipment', 'project', 'person']

function defaultAttributes(domain: EntityDomain): EntityAttributes {
  switch (domain) {
    case 'home':
      return { domain: 'home', entity_type: 'room' }
    case 'vehicle':
      return { domain: 'vehicle', make: '', model: '', year: new Date().getFullYear() }
    case 'equipment':
      return { domain: 'equipment' }
    case 'project':
      return { domain: 'project', related_entity_ids: [] }
    case 'person':
      return { domain: 'person' }
  }
}

function textOrNull(value: string): string | null {
  return value.trim() === '' ? null : value
}

interface Props {
  initialEntity?: Entity
  onSubmit: (input: EntityCreateInput) => void
  isSubmitting?: boolean
  submitError?: string | null
  submitLabel?: string
}

export function EntityForm({ initialEntity, onSubmit, isSubmitting, submitError, submitLabel }: Props) {
  const isEdit = initialEntity !== undefined
  const [domain, setDomain] = useState<EntityDomain>(initialEntity?.domain ?? 'vehicle')
  const [name, setName] = useState(initialEntity?.name ?? '')
  const [status, setStatus] = useState(initialEntity?.status ?? STATUS_BY_DOMAIN[domain][0])
  const [location, setLocation] = useState(initialEntity?.location ?? '')
  const [tagsInput, setTagsInput] = useState(initialEntity?.tags?.join(', ') ?? '')
  const [attributes, setAttributes] = useState<EntityAttributes>(
    initialEntity?.attributes ?? defaultAttributes(domain),
  )

  function handleDomainChange(next: EntityDomain) {
    setDomain(next)
    setStatus(STATUS_BY_DOMAIN[next][0])
    setAttributes(defaultAttributes(next))
  }

  function updateAttrs(patch: Partial<EntityAttributes>) {
    setAttributes((prev) => ({ ...prev, ...patch }) as EntityAttributes)
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const tags = tagsInput
      .split(',')
      .map((t) => t.trim())
      .filter((t) => t.length > 0)

    onSubmit({
      domain,
      name,
      status,
      location: textOrNull(location),
      tags,
      attributes,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <label className="block">
          <span className="text-sm font-medium">Domain</span>
          <select
            value={domain}
            disabled={isEdit}
            onChange={(e) => handleDomainChange(e.target.value as EntityDomain)}
            className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2 disabled:opacity-60"
          >
            {DOMAINS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-sm font-medium">Status</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2"
          >
            {STATUS_BY_DOMAIN[domain].map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="block">
        <span className="text-sm font-medium">Name</span>
        <input
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={
            domain === 'vehicle' ? '2021 Ford Ranger' : domain === 'person' ? 'Full name' : 'Display name'
          }
          className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2"
        />
      </label>

      <div className="grid grid-cols-2 gap-4">
        <label className="block">
          <span className="text-sm font-medium">Location</span>
          <input
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder={domain === 'person' ? 'City, neighborhood, ...' : 'Garage, kitchen, ...'}
            className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium">Tags</span>
          <input
            value={tagsInput}
            onChange={(e) => setTagsInput(e.target.value)}
            placeholder="comma, separated"
            className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2"
          />
        </label>
      </div>

      <fieldset className="rounded-md border border-divider p-3">
        <legend className="text-sm font-medium px-1">{domain} details</legend>
        <div className="grid grid-cols-2 gap-4">
          {attributes.domain === 'home' && (
            <>
              <label className="block">
                <span className="text-sm">Type</span>
                <select
                  value={attributes.entity_type}
                  onChange={(e) => updateAttrs({ entity_type: e.target.value as never })}
                  className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
                >
                  {['room', 'system', 'appliance', 'structure'].map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <TextField label="Make" value={attributes.make ?? ''} onChange={(v) => updateAttrs({ make: textOrNull(v) })} />
              <TextField label="Model" value={attributes.model ?? ''} onChange={(v) => updateAttrs({ model: textOrNull(v) })} />
              <TextField label="Serial number" value={attributes.serial_number ?? ''} onChange={(v) => updateAttrs({ serial_number: textOrNull(v) })} />
              <TextField label="Paint brand" value={attributes.paint_brand ?? ''} onChange={(v) => updateAttrs({ paint_brand: textOrNull(v) })} />
              <TextField label="Paint code" value={attributes.paint_code ?? ''} onChange={(v) => updateAttrs({ paint_code: textOrNull(v) })} />
              <DateField label="Install date" value={attributes.install_date ?? ''} onChange={(v) => updateAttrs({ install_date: v || null })} />
              <DateField label="Warranty expires" value={attributes.warranty_expires_at ?? ''} onChange={(v) => updateAttrs({ warranty_expires_at: v || null })} />
            </>
          )}

          {attributes.domain === 'vehicle' && (
            <>
              <TextField required label="Make" value={attributes.make} onChange={(v) => updateAttrs({ make: v })} />
              <TextField required label="Model" value={attributes.model} onChange={(v) => updateAttrs({ model: v })} />
              <NumberField required label="Year" value={attributes.year} onChange={(v) => updateAttrs({ year: v })} />
              <TextField label="VIN" value={attributes.vin ?? ''} onChange={(v) => updateAttrs({ vin: textOrNull(v) })} />
              <TextField label="License plate" value={attributes.license_plate ?? ''} onChange={(v) => updateAttrs({ license_plate: textOrNull(v) })} />
              <NumberField label="Current mileage" value={attributes.current_mileage ?? undefined} onChange={(v) => updateAttrs({ current_mileage: v ?? null })} />
              <DateField label="Purchase date" value={attributes.purchase_date ?? ''} onChange={(v) => updateAttrs({ purchase_date: v || null })} />
            </>
          )}

          {attributes.domain === 'equipment' && (
            <>
              <TextField label="Make" value={attributes.make ?? ''} onChange={(v) => updateAttrs({ make: textOrNull(v) })} />
              <TextField label="Model" value={attributes.model ?? ''} onChange={(v) => updateAttrs({ model: textOrNull(v) })} />
              <TextField label="Serial number" value={attributes.serial_number ?? ''} onChange={(v) => updateAttrs({ serial_number: textOrNull(v) })} />
              <DateField label="Purchase date" value={attributes.purchase_date ?? ''} onChange={(v) => updateAttrs({ purchase_date: v || null })} />
            </>
          )}

          {attributes.domain === 'project' && (
            <>
              <DateField label="Start date" value={attributes.start_date ?? ''} onChange={(v) => updateAttrs({ start_date: v || null })} />
              <DateField label="Target end date" value={attributes.target_end_date ?? ''} onChange={(v) => updateAttrs({ target_end_date: v || null })} />
              <DateField label="Completed date" value={attributes.completed_date ?? ''} onChange={(v) => updateAttrs({ completed_date: v || null })} />
              <NumberField label="Budget estimate" value={attributes.budget_estimate ?? undefined} onChange={(v) => updateAttrs({ budget_estimate: v ?? null })} />
            </>
          )}

          {attributes.domain === 'person' && (
            <>
              <TextField label="Relationship" value={attributes.relationship ?? ''} onChange={(v) => updateAttrs({ relationship: textOrNull(v) })} />
              <TextField label="Company" value={attributes.company ?? ''} onChange={(v) => updateAttrs({ company: textOrNull(v) })} />
              <TextField label="Job title" value={attributes.job_title ?? ''} onChange={(v) => updateAttrs({ job_title: textOrNull(v) })} />
              <TextField label="Email" value={attributes.email ?? ''} onChange={(v) => updateAttrs({ email: textOrNull(v) })} />
              <TextField label="Phone" value={attributes.phone ?? ''} onChange={(v) => updateAttrs({ phone: textOrNull(v) })} />
              <DateField label="Birthday" value={attributes.birthday ?? ''} onChange={(v) => updateAttrs({ birthday: v || null })} />
            </>
          )}
        </div>
      </fieldset>

      {submitError && <p className="text-sm text-red-500">{submitError}</p>}

      <button
        type="submit"
        disabled={isSubmitting}
        className="rounded-md bg-primary text-white hover:bg-primary-hover px-4 py-2 font-medium disabled:opacity-50"
      >
        {isSubmitting ? 'Saving…' : (submitLabel ?? (isEdit ? 'Save changes' : 'Create entity'))}
      </button>
    </form>
  )
}

function TextField({
  label,
  value,
  onChange,
  required,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  required?: boolean
}) {
  return (
    <label className="block">
      <span className="text-sm">{label}</span>
      <input
        required={required}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
      />
    </label>
  )
}

function NumberField({
  label,
  value,
  onChange,
  required,
}: {
  label: string
  value: number | undefined
  onChange: (value: number | undefined) => void
  required?: boolean
}) {
  return (
    <label className="block">
      <span className="text-sm">{label}</span>
      <input
        type="number"
        required={required}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))}
        className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
      />
    </label>
  )
}

function DateField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="block">
      <span className="text-sm">{label}</span>
      <input
        type="date"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
      />
    </label>
  )
}
