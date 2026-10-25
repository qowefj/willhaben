// Private Apify actor "willhaben-full".
// mode "list":   crawl Willhaben search URLs (all pages) + every advert detail page -> dataset (full advertDetails JSON)
// mode "images": download a list of {key, url} images -> default key-value store under `key`
import { Actor, log } from 'apify';
import { CheerioCrawler, BasicCrawler } from 'crawlee';

await Actor.init();
const input = (await Actor.getInput()) || {};
const {
    mode = 'list',
    searchUrls = [],
    detailUrls = [],
    images = [],
    maxPages = 20,
    maxDetails = 2000,
    pushSummaries = false,  // also push {summary:true,id,title,price} for every search-result ad
    skipIds = [],           // already-known advert ids: no detail request (monitor mode)
    proxy = { useApifyProxy: true },
} = input;

const proxyConfiguration = proxy && proxy.useApifyProxy ? await Actor.createProxyConfiguration(proxy) : undefined;

const nextData = ($) => {
    const raw = $('script#__NEXT_DATA__').html();
    if (!raw) throw new Error(`No __NEXT_DATA__ (blocked?) title="${$('title').text()}"`);
    return JSON.parse(raw).props.pageProps;
};

if (mode === 'list') {
    let detailCount = 0;
    const skip = new Set(skipIds.map(String));
    const crawler = new CheerioCrawler({
        proxyConfiguration,
        maxConcurrency: 6,
        maxRequestRetries: 4,
        requestHandlerTimeoutSecs: 60,
        async requestHandler({ request, $, crawler: c }) {
            const props = nextData($);
            if (request.userData.label === 'DETAIL') {
                const ad = props.advertDetails;
                if (!ad) throw new Error('No advertDetails');
                await Actor.pushData({ id: String(ad.id), url: request.url, advertDetails: ad });
                return;
            }
            const sr = props.searchResult;
            const ads = sr?.advertSummaryList?.advertSummary || [];
            const page = Number(new URL(request.url).searchParams.get('page') || 1);
            log.info(`Search page ${page}: ${ads.length} ads, rowsFound=${sr?.rowsFound}`);
            for (const ad of ads) {
                const attr = (n) => ad.attributes.attribute.find((a) => a.name === n)?.values?.[0];
                const seo = attr('SEO_URL');
                if (pushSummaries) {
                    await Actor.pushData({ summary: true, id: String(ad.id), title: ad.description, price: attr('PRICE'), seo });
                }
                if (!seo || detailCount >= maxDetails || skip.has(String(ad.id))) continue;
                detailCount++;
                await c.addRequests([{ url: `https://www.willhaben.at/iad/${seo}`, userData: { label: 'DETAIL' } }]);
            }
            const rows = sr?.rowsRequested || ads.length;
            if (ads.length && page * rows < sr.rowsFound && page < maxPages) {
                const next = new URL(request.url);
                next.searchParams.set('page', String(page + 1));
                await c.addRequests([{ url: next.toString(), userData: { label: 'SEARCH' } }]);
            }
        },
        failedRequestHandler({ request, error }) {
            log.warning(`FAILED ${request.url}: ${error.message}`);
        },
    });
    await crawler.run([
        ...searchUrls.map((url) => ({ url, userData: { label: 'SEARCH' } })),
        ...detailUrls.map((url) => ({ url, userData: { label: 'DETAIL' } })),
    ]);
} else if (mode === 'images') {
    let ok = 0;
    const crawler = new BasicCrawler({
        maxConcurrency: 10,
        maxRequestRetries: 3,
        async requestHandler({ request, sendRequest }) {
            const proxyUrl = proxyConfiguration ? await proxyConfiguration.newUrl() : undefined;
            const res = await sendRequest({
                url: request.url, responseType: 'buffer', proxyUrl,
                headers: { accept: 'image/jpeg,image/png;q=0.9,image/*;q=0.5' },
            });
            if (res.statusCode !== 200) throw new Error(`HTTP ${res.statusCode}`);
            const ct = (res.headers['content-type'] || 'image/jpeg').split(';')[0];
            await Actor.setValue(request.userData.key, res.body, { contentType: ct });
            ok++;
        },
        failedRequestHandler({ request, error }) {
            log.warning(`FAILED image ${request.userData.key}: ${error.message}`);
        },
    });
    await crawler.run(images.map((i) => ({ url: i.url, uniqueKey: i.key, userData: { key: i.key } })));
    log.info(`Downloaded ${ok}/${images.length} images`);
}

await Actor.exit();
