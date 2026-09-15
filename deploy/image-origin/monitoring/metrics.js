// A fixed, bounded set of counters. Never use request data as a metric key.
const classes = ['2xx', '3xx', '4xx', '5xx'];
function count(r) {
    try {
        const metrics = ngx.shared.origin_metrics;
        const statusClass = Math.floor(r.status / 100) + 'xx';
        if (classes.indexOf(statusClass) >= 0) metrics.incr(statusClass, 1, 0);
        if (r.status === 429) metrics.incr('limited', 1, 0);
        if (r.status === 403 && r.variables.origin_authenticated === '0') {
            metrics.incr('auth_rejected', 1, 0);
        }
        const seconds = Number(r.variables.request_time);
        if (isFinite(seconds) && seconds >= 0) {
            metrics.incr('duration_sum', seconds, 0);
            metrics.incr('duration_count', 1, 0);
        }
    } catch (_) { /* Observability must never interrupt image responses. */ }
}
function expose(r) {
    const metrics = ngx.shared.origin_metrics;
    const lines = classes.map(c =>
        `image_origin_responses_total{status_class="${c}"} ${metrics.get(c) || 0}`);
    ['limited', 'auth_rejected'].forEach(name => {
        lines.push(`image_origin_${name}_total ${metrics.get(name) || 0}`);
    });
    ['duration_sum', 'duration_count'].forEach(name => {
        lines.push(`image_origin_${name} ${metrics.get(name) || 0}`);
    });
    r.headersOut['Content-Type'] = 'text/plain; version=0.0.4';
    r.return(200, lines.join('\n') + '\n');
}
export default {count, expose};
