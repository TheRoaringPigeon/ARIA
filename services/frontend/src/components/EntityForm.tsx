import { useState, type FormEvent } from 'react'
import type { EntityCreateInput } from '../api/entities'
import type { Entity } from '../api/types'
import { DOMAIN_REGISTRY, DOMAINS, type EntityAttributes, type EntityDomain, type FieldConfig } from '../domains'

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
  const [status, setStatus] = useState(initialEntity?.status ?? DOMAIN_REGISTRY[domain].statuses[0])
  const [location, setLocation] = useState(initialEntity?.location ?? '')
  const [tagsInput, setTagsInput] = useState(initialEntity?.tags?.join(', ') ?? '')
  const [attributes, setAttributes] = useState<EntityAttributes>(
    initialEntity?.attributes ?? DOMAIN_REGISTRY[domain].defaultAttributes(),
  )

  function handleDomainChange(next: EntityDomain) {
    setDomain(next)
    setStatus(DOMAIN_REGISTRY[next].statuses[0])
    setAttributes(DOMAIN_REGISTRY[next].defaultAttributes())
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

  const domainConfig = DOMAIN_REGISTRY[domain]

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
            {domainConfig.statuses.map((s) => (
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
          placeholder={domainConfig.namePlaceholder}
          className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2"
        />
      </label>

      <div className="grid grid-cols-2 gap-4">
        <label className="block">
          <span className="text-sm font-medium">Location</span>
          <input
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder={domainConfig.locationPlaceholder}
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
          <AttrFields fields={domainConfig.fields} attributes={attributes} onChange={updateAttrs} />
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

function AttrFields({
  fields,
  attributes,
  onChange,
}: {
  fields: readonly FieldConfig[]
  attributes: EntityAttributes
  onChange: (patch: Partial<EntityAttributes>) => void
}) {
  const values = attributes as unknown as Record<string, unknown>

  return (
    <>
      {fields.map((field) => {
        const value = values[field.key]
        const patch = (v: unknown) => onChange({ [field.key]: v } as Partial<EntityAttributes>)

        if (field.kind === 'select') {
          return (
            <SelectField
              key={field.key}
              label={field.label}
              required={field.required}
              value={(value as string) ?? field.options?.[0] ?? ''}
              options={field.options ?? []}
              onChange={patch}
            />
          )
        }
        if (field.kind === 'number') {
          return (
            <NumberField
              key={field.key}
              label={field.label}
              required={field.required}
              value={value as number | undefined}
              onChange={(v) => patch(field.required ? v : (v ?? null))}
            />
          )
        }
        if (field.kind === 'date') {
          return (
            <DateField
              key={field.key}
              label={field.label}
              value={(value as string) ?? ''}
              onChange={(v) => patch(v || null)}
            />
          )
        }
        return (
          <TextField
            key={field.key}
            label={field.label}
            required={field.required}
            value={(value as string) ?? ''}
            onChange={(v) => patch(field.required ? v : textOrNull(v))}
          />
        )
      })}
    </>
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

function SelectField({
  label,
  value,
  onChange,
  options,
  required,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: readonly string[]
  required?: boolean
}) {
  return (
    <label className="block">
      <span className="text-sm">{label}</span>
      <select
        required={required}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  )
}
