() => {
    const links = Array.from(document.querySelectorAll('a[href*="/in/"]'));
    const seen = new Set();
    const cards = [];

    for (const link of links) {
        const href = link.href.split('?')[0];
        if (seen.has(href)) continue;
        seen.add(href);

        let p = link.parentElement;
        let card = null;
        for (let i = 0; i < 15; i++) {
            if (!p) break;
            const text = (p.innerText || '').trim();
            if (text.length > 30 && text.split('\n').length >= 2) {
                card = p;
                break;
            }
            p = p.parentElement;
        }

        if (card) {
            const imgs = Array.from(card.querySelectorAll('img')).map(function(i) { return i.alt; });
            const lines = card.innerText.split('\n').map(function(l) { return l.trim(); }).filter(function(l) { return l.length > 0; });
            cards.push({
                href: href,
                lines: lines.slice(0, 8),
                img_alts: imgs,
                card_tag: card.tagName,
                card_classes: card.className.substring(0, 150),
            });
        }
        if (cards.length >= 4) break;
    }
    return cards;
}
