/* Shared Tailwind Play CDN config for the Cozy Dashboard operator lane.
   Preflight is OFF so it never resets the shipped design; all theme tokens
   resolve to the CSS custom properties defined in cozy-theme.css, so colour /
   type / radius stay centralized in one place. Load order per page:
     1) <link cozy-theme.css>  (defines :root tokens + base)
     2) <script cdn.tailwindcss.com>
     3) <script src="cozy-tailwind.js">  (this file; reads those tokens) */
tailwind.config = {
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        root:'var(--bg-root)', graphite:'var(--bg-primary)', panel:'var(--bg-secondary)',
        shelf:'var(--bg-tertiary)', raised:'var(--bg-elevated)',
        ink:'var(--text-primary)', 'ink-2':'var(--text-secondary)',
        'ink-3':'var(--text-tertiary)', 'ink-4':'var(--text-muted)',
        amber:'var(--accent)', 'amber-soft':'var(--accent-soft)',
        led:{ green:'var(--green)', yellow:'var(--yellow)', red:'var(--red)',
              blue:'var(--blue)', violet:'var(--violet)' },
      },
      fontFamily: {
        display:['Space Grotesk','sans-serif'],
        sans:['Space Grotesk','sans-serif'],
        mono:['JetBrains Mono','ui-monospace','monospace'],
      },
      borderColor: {
        hair:'var(--border)', 'hair-mid':'var(--border-mid)', 'hair-bright':'var(--border-bright)',
      },
      borderRadius: {
        sm:'var(--radius-sm)', DEFAULT:'var(--radius)',
        lg:'var(--radius-lg)', xl:'var(--radius-xl)',
      },
      boxShadow: {
        panel:'var(--shadow-panel)', pop:'var(--shadow-pop)',
      },
    },
  },
};
