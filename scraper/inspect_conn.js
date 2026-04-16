() => {
    const links = Array.from(document.querySelectorAll('a[href*="/in/"]'));
    const seen = new Set();
    const cards = [];

    for (const link of links) {
        const href = link.href.split('?')[0];
        if (!href.includes('/in/') || seen.has(href)) continue;
        seen.add(href);

        // Walk up to find a container with name + title text
        let container = null;
        let p = link.parentElement;
        for (let i = 0; i < 15; i++) {
            if (!p) break;
            const lines = (p.innerText || '').trim().split('\n').map(function(l) { return l.trim(); }).filter(function(l) { return l.length > 0; });
            if (lines.length >= 2) {
                container = p;
                break;
            }
            p = p.parentElement;
        }

        if (!container) continue;

        const lines = container.innerText.split('\n').map(function(l) { return l.trim(); }).filter(function(l) { return l.length > 0; });

        // name = first line
        const name = lines[0] || '';

        // title = lines between name and "Connectie" / "Bericht" / "Connect"
        const stopWords = ['Connectie', 'Bericht', 'Connect', 'Message', 'Follow', 'Volgen'];
        const titleLines = [];
        for (let i = 1; i < lines.length; i++) {
            if (stopWords.some(function(w) { return lines[i].startsWith(w); })) break;
            titleLines.push(lines[i]);
        }
        const title = titleLines.join(' | ');

        cards.push({
            profile_url: href,
            name: name,
            title: title,
        });
    }
    return cards;
}
