/** @type {import('tailwindcss').Config} */
const plugin = require('tailwindcss/plugin');
const defaults = require('tailwindcss/colors');

// Colors are CSS variables, so the theme switch only changes their values (dark is the default, light inverts the scale).
const PALETTES = ['slate', 'sky', 'violet', 'rose', 'amber', 'emerald', 'orange'];
const SHADES = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950'];
const INVERTED = { 50: 950, 100: 900, 200: 800, 300: 700, 400: 600, 500: 500, 600: 400, 700: 300, 800: 200, 900: 100, 950: 50 };

// slate-500 carries the faint text (helper copy, placeholders, meta). Tailwind's own value reaches only 4.2:1 on the dark
// page and 3.8:1 on slate-800 fills, so both themes use a value that gives at least 4.5:1 on the page and on cards.
const FAINT_TEXT = { dark: '#7183a0', light: '#5c6b82' };

const toRgb = hex => {
  const value = parseInt(hex.slice(1), 16);
  return `${value >> 16} ${(value >> 8) & 255} ${value & 255}`;
};

const tokens = theme => {
  const vars = {};
  for (const name of PALETTES) {
    for (const shade of SHADES) {
      const source = theme === 'dark' ? shade : INVERTED[shade];
      vars[`--${name}-${shade}`] = toRgb(defaults[name][source]);
    }
  }
  vars['--slate-500'] = toRgb(FAINT_TEXT[theme]);
  vars['--white'] = theme === 'dark' ? '255 255 255' : '15 23 42';
  return vars;
};

const themed = name => Object.fromEntries(SHADES.map(shade => [shade, `rgb(var(--${name}-${shade}) / <alpha-value>)`]));

module.exports = {
  content: ['./chronicle/templates/**/*.html'],
  theme: {
    extend: {
      colors: {
        ...Object.fromEntries(PALETTES.map(name => [name, themed(name)])),
        white: 'rgb(var(--white) / <alpha-value>)',
        // Text on the bright accent buttons stays dark in both themes.
        ink: 'rgb(2 6 23 / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
    plugin(({ addBase }) => {
      addBase({
        ':root': { ...tokens('dark'), colorScheme: 'dark' },
        ':root[data-theme="light"]': { ...tokens('light'), colorScheme: 'light' },
        '@media (prefers-color-scheme: light)': {
          ':root:not([data-theme="dark"])': { ...tokens('light'), colorScheme: 'light' },
        },
      });
    }),
  ],
};
