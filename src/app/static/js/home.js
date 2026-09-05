// The template's own landing page. Replaced by the app's real home page during
// a build — it exists so a fresh clone shows something useful.
const app = document.getElementById('app');

const SECTIONS = [
  {
    heading: 'First-Time Setup',
    items: [
      'Clone this repo',
      ['Run ', 'cp env.example .env', ' and set ', 'SESSION_SECRET'],
      ['Run ', './install-claude.sh', ' and/or ', './install-opencode.sh'],
      'Open your AI tool in this directory',
    ],
  },
  {
    heading: 'Before /build',
    items: [
      ['Fill in ', 'SEED.md', ' with app name, features, auth requirements'],
      ['.env', ' has the API keys for any integration checked in SEED.md'],
    ],
  },
  {
    heading: 'Before /launch',
    items: [
      ['App reviewed and working at ', 'http://localhost:[APP_PORT]'],
      ['Set ', 'TUNNEL_TOKEN', ' in ', '.env'],
      'Domain configured in Cloudflare dashboard (Zero Trust → Tunnels)',
    ],
  },
];

// Odd indices are rendered as code. textContent throughout — never innerHTML.
function renderItem(parts) {
  const li = document.createElement('li');
  li.className = 'text-sm text-gray-600 leading-relaxed';
  for (const [i, part] of [].concat(parts).entries()) {
    if (i % 2 === 1) {
      const c = document.createElement('code');
      c.className = 'font-mono text-gray-700 bg-gray-100 rounded px-1 py-0.5 text-xs';
      c.textContent = part;
      li.appendChild(c);
    } else {
      li.appendChild(document.createTextNode(part));
    }
  }
  return li;
}

function render() {
  const frag = document.createDocumentFragment();

  const title = document.createElement('h1');
  title.className = 'text-2xl font-semibold tracking-tight mb-1';
  title.textContent = 'black-box';
  frag.appendChild(title);

  const sub = document.createElement('p');
  sub.className = 'text-sm text-gray-500 mb-8';
  sub.textContent = 'Python · FastAPI · Vanilla JS · SQLite';
  frag.appendChild(sub);

  for (const section of SECTIONS) {
    const card = document.createElement('section');
    card.className = 'bg-white border border-gray-200 rounded-lg p-5 mb-4';

    const h = document.createElement('h2');
    h.className = 'text-sm font-semibold mb-3';
    h.textContent = section.heading;
    card.appendChild(h);

    const ul = document.createElement('ul');
    ul.className = 'space-y-2 list-disc pl-5';
    for (const item of section.items) ul.appendChild(renderItem(item));
    card.appendChild(ul);
    frag.appendChild(card);
  }

  app.replaceChildren(frag);
}

render();
