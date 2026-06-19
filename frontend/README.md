# Frontend (React + TypeScript + Tailwind + shadcn-style)

This folder holds the **React source** for `AgentPlanning`. The live Kapruka
concierge at `public/index.html` is still a zero-build vanilla app on Vercel;
the planning UI is already integrated there as a vanilla port.

## Why `/components/ui`?

shadcn/ui expects reusable primitives under `components/ui/`. Keeping that
path makes it straightforward to add more shadcn components later and to run
`npx shadcn@latest add <component>` without reconfiguring aliases.

## Setup (first time)

```bash
cd frontend
npm install
```

### Optional: full shadcn CLI init

If you want the official shadcn scaffold (recommended when migrating the whole
app to React):

```bash
cd frontend
npx shadcn@latest init
npx shadcn@latest add button card
```

Use the aliases from `components.json`:

- `@/components` → `frontend/components`
- `@/lib/utils` → `frontend/lib/utils.ts`

## Components added

| File | Purpose |
|------|---------|
| `components/ui/ai-planning.tsx` | Minimal counter example from the prompt |
| `components/ui/agent-planning.tsx` | Full `AgentPlanning` timeline component |
| `lib/utils.ts` | `cn()` helper (clsx + tailwind-merge) |

## Dependencies

- `react`, `react-dom`, `typescript`
- `tailwindcss`, `postcss`, `autoprefixer`
- `lucide-react` (icons used by `AgentPlanning`)
- `clsx`, `tailwind-merge` (for `cn()`)

## Run the React demo locally

```bash
cd frontend
npm run dev
```

Open the Vite dev URL to preview the standalone `AgentPlanning` card.

## Build output

```bash
cd frontend
npm run build
```

Builds to `public/react/` (see `vite.config.ts`). The main concierge does **not**
depend on this build today.

## Integration in the live app

`public/index.html` includes a vanilla port of `AgentPlanning` shown during
`/api/search` calls:

1. Understand request (with Langbly translation for si/ta)
2. Search Kapruka catalog
3. Pick best matches
4. Prepare localized reply + voice

The crowd canvas remains as a translucent background behind the planning card.

## Migrating the full app to React (future)

1. Move concierge UI from `public/index.html` into `frontend/src/`
2. Add a Vercel build step: `cd frontend && npm ci && npm run build`
3. Serve `public/react/index.html` as the app shell, or adopt Next.js

Until then, Python APIs in `api/` continue to work unchanged.
