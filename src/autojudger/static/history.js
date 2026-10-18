const $ = (id) => document.getElementById(id);
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false
});
const verdictLabels = { 'strong reject': '强烈拒绝', reject: '拒绝',
  'weak reject': '倾向拒绝', 'weak accept': '倾向接受',
  accept: '接受', 'strong accept': '强烈接受' };
const stabilityLabels = { high: '高', medium: '中', low: '低' };
let reportToDownload = null;
let nextOffset = 0;
const pageSize = 30;
let lastLiveIds = null;
const phaseLabels = {
  queued: '等待处理',
  analyzing: '分析题目与标准程序',
  clarity: '评估题面清晰度',
  searching: '寻找 Codeforces 参考题',
  collecting: '读取题面与官方题解',
  ranking: '匿名排序与校准'
};

function displayDate(seconds) {
  return Number.isFinite(Number(seconds)) ? dateFormatter.format(new Date(Number(seconds) * 1000)) : '时间未知';
}

function showMessage(id, message) {
  $(id).textContent = message;
  $(id).classList.remove('hidden');
}

function allowedLink(url, label) {
  if (typeof url !== 'string') return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || !['codeforces.com', 'github.com', 'raw.githubusercontent.com'].includes(parsed.hostname)) return null;
    const link = document.createElement('a');
    link.href = parsed.href;
    link.textContent = label;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    return link;
  } catch { return null; }
}

function renderListItem(item, position) {
  const link = document.createElement('a');
  link.className = 'history-row';
  link.href = `/history/${encodeURIComponent(item.id)}`;
  const index = document.createElement('span');
  index.className = 'history-row-index';
  index.textContent = String(position).padStart(2, '0');
  const main = document.createElement('span');
  main.className = 'history-row-main';
  const title = document.createElement('strong');
  title.textContent = item.title || '未命名题目';
  const date = document.createElement('small');
  date.textContent = `${displayDate(item.created_at)} · ${item.reference_count ?? '—'} 道参考题`;
  main.append(title, date);
  const scores = document.createElement('span');
  scores.className = 'history-row-scores';
  for (const [label, value] of [['CF rating', item.score ?? '—'], ['题面清晰度', `${item.clarity_score ?? '—'} / 5`]]) {
    const cell = document.createElement('span');
    const name = document.createElement('small'); name.textContent = label;
    const number = document.createElement('b'); number.textContent = String(value);
    cell.append(name, number); scores.append(cell);
  }
  const arrow = document.createElement('span');
  arrow.className = 'history-row-arrow'; arrow.textContent = '↗';
  link.append(index, main, scores, arrow);
  return link;
}

function renderLiveItem(item) {
  const link = document.createElement('a');
  link.className = 'history-row live-row';
  link.href = `/history/${encodeURIComponent(item.id)}`;
  const icon = document.createElement('span');
  icon.className = 'history-row-index live-index';
  icon.textContent = '↻';
  const main = document.createElement('span');
  main.className = 'history-row-main';
  const title = document.createElement('strong');
  title.textContent = item.status === 'queued' ? '任务已提交 · 等待处理' : '正在评测题目';
  const date = document.createElement('small');
  date.textContent = `${displayDate(item.created_at)} · ${phaseLabels[item.phase] || '处理中'}`;
  main.append(title, date);
  const progress = document.createElement('span');
  progress.className = 'live-progress';
  progress.textContent = item.total > 0 ? `${item.completed} / ${item.total}` : '进行中';
  const arrow = document.createElement('span');
  arrow.className = 'history-row-arrow';
  arrow.textContent = '↗';
  link.append(icon, main, progress, arrow);
  return link;
}

async function refreshLive() {
  try {
    const response = await fetch('/api/jobs', { cache: 'no-store' });
    if (!response.ok) return;
    const data = await response.json();
    const list = $('live-list');
    list.replaceChildren(...data.items.map(renderLiveItem));
    $('live-total').textContent = String(data.total);
    $('live-empty').classList.toggle('hidden', data.total > 0);
    const badge = $('sidebar-live-count');
    badge.textContent = String(data.total);
    badge.classList.toggle('hidden', data.total === 0);
    const ids = data.items.map((item) => item.id).sort().join(',');
    if (lastLiveIds !== null && lastLiveIds !== ids) {
      nextOffset = 0;
      $('history-list').replaceChildren();
      $('history-error').classList.add('hidden');
      loadHistory();
    }
    lastLiveIds = ids;
  } catch { /* Keep the previous snapshot during a transient network error. */ }
}

async function loadHistory() {
  const button = $('history-more');
  button.disabled = true;
  try {
    const response = await fetch(`/api/history?limit=${pageSize}&offset=${nextOffset}`);
    if (!response.ok) throw new Error('无法读取评测历史，请稍后重试。');
    const page = await response.json();
    $('history-total').textContent = `已收录 ${page.total} 次评测`;
    if (page.total === 0) showMessage('history-error', '还没有完成的评测。提交一道题后，报告会出现在这里。');
    for (const [index, item] of page.items.entries()) {
      $('history-list').append(renderListItem(item, nextOffset + index + 1));
    }
    nextOffset += page.items.length;
    button.classList.toggle('hidden', nextOffset >= page.total);
  } catch (error) {
    showMessage('history-error', error.message || '无法读取评测历史。');
  } finally {
    button.disabled = false;
  }
}

function renderCriteria(clarity) {
  const host = $('detail-criteria');
  host.replaceChildren();
  for (const criterion of clarity.criteria || []) {
    const row = document.createElement('div'); row.className = 'history-criterion';
    row.classList.toggle('is-reject', String(criterion.verdict || '').includes('reject'));
    const maxScore = Number(criterion.max_score) > 0 ? Number(criterion.max_score)
      : Number(clarity.max_score) > 0 ? Number(clarity.max_score) : 5;
    const numericScore = Number(criterion.score);
    const hasScore = criterion.score !== null && criterion.score !== undefined
      && criterion.score !== '' && Number.isFinite(numericScore);
    const ring = document.createElement('div');
    ring.className = 'history-criterion-ring';
    ring.style.setProperty('--criterion-angle',
      `${hasScore ? Math.max(0, Math.min(360, numericScore / maxScore * 360)) : 0}deg`);
    ring.setAttribute('role', 'progressbar');
    ring.setAttribute('aria-label', criterion.label || criterion.key || '评分项');
    ring.setAttribute('aria-valuemin', '0');
    ring.setAttribute('aria-valuemax', String(maxScore));
    if (hasScore) ring.setAttribute('aria-valuenow', String(numericScore));
    const ringInner = document.createElement('div');
    const score = document.createElement('strong');
    score.textContent = hasScore ? String(criterion.score) : '—';
    const denominator = document.createElement('small');
    denominator.textContent = `/${maxScore}`;
    ringInner.append(score, denominator);
    ring.append(ringInner);
    const top = document.createElement('div'); top.className = 'history-criterion-head';
    const copy = document.createElement('div'); copy.className = 'history-criterion-copy';
    const label = document.createElement('strong'); label.className = 'history-criterion-label';
    label.textContent = criterion.label || criterion.key || '评分项';
    const meta = document.createElement('div'); meta.className = 'history-criterion-meta';
    const weight = document.createElement('span');
    weight.textContent = `权重 ${Math.round((criterion.weight || 0) * 100)}%`;
    const verdict = document.createElement('span');
    verdict.className = 'history-criterion-verdict';
    verdict.textContent = verdictLabels[criterion.verdict] || criterion.verdict || '未评定';
    meta.append(weight, verdict);
    copy.append(label, meta);
    top.append(ring, copy);
    const reason = document.createElement('p'); reason.textContent = criterion.reason || '';
    row.append(top, reason); host.append(row);
  }
}

function renderRanking(report) {
  const host = $('detail-ranking');
  host.replaceChildren();
  for (const item of report.ranking || []) {
    const row = document.createElement('li');
    if (item.target) row.classList.add('target');
    const rank = document.createElement('strong'); rank.textContent = String(item.rank).padStart(2, '0');
    const title = document.createElement('span'); title.className = 'rank-title';
    const rating = document.createElement('span'); rating.className = 'rank-rating';
    if (item.target) {
      title.textContent = '你的题目';
      rating.textContent = `≈ CF ${report.score}`;
    } else {
      const problemLink = allowedLink(item.url, `${item.codeforces_id} · ${item.title}`);
      if (problemLink) title.append(problemLink);
      else title.textContent = `${item.codeforces_id} · ${item.title}`;
      const editorialLink = allowedLink(item.editorial_url, ' · 官方题解 ↗');
      if (editorialLink) title.append(editorialLink);
      rating.textContent = `CF ${item.rating}`;
    }
    row.append(rank, title, rating); host.append(row);
  }
}

function renderDetail(entry) {
  const report = entry.report || {};
  reportToDownload = report;
  const analysis = report.analysis || {};
  const clarity = report.statement_clarity || {};
  const calibration = report.calibration || {};
  $('detail-title').textContent = analysis.summary || '未命名题目';
  $('detail-date').textContent = `${displayDate(entry.created_at)} · 历史记录`;
  $('detail-rating').textContent = report.score ?? '—';
  const clarityMax = Number(clarity.max_score) > 0 ? Number(clarity.max_score) : 5;
  const clarityScore = Number(clarity.score);
  const hasClarityScore = clarity.score !== null && clarity.score !== undefined
    && clarity.score !== '' && Number.isFinite(clarityScore);
  $('detail-clarity').textContent = hasClarityScore ? String(clarity.score) : '—';
  $('detail-clarity-max').textContent = `/ ${clarityMax}`;
  const clarityRing = $('detail-clarity-ring');
  const sectionRing = $('detail-section-ring');
  const ringAngle = hasClarityScore ? Math.max(0, Math.min(360, clarityScore / clarityMax * 360)) : 0;
  for (const ring of [clarityRing, sectionRing]) {
    ring.style.setProperty('--clarity-angle', `${ringAngle}deg`);
    ring.setAttribute('aria-valuemax', String(clarityMax));
    if (hasClarityScore) ring.setAttribute('aria-valuenow', String(clarityScore));
    else ring.removeAttribute('aria-valuenow');
  }
  $('detail-section-score').textContent = hasClarityScore ? String(clarity.score) : '—';
  $('detail-section-max').textContent = `/ ${clarityMax}`;
  const verdictText = clarity.verdict ? `${clarity.verdict} · ${verdictLabels[clarity.verdict] || ''}` : '评分档位未知';
  $('detail-verdict').textContent = verdictText;
  $('detail-section-verdict').textContent = verdictText;
  $('detail-section-verdict').classList.toggle('failed', Boolean(clarity.verdict?.includes('reject')));
  $('detail-references').textContent = report.reference_count ?? '—';
  $('detail-stability').textContent = `盲排稳定性：${stabilityLabels[calibration.stability] || '未知'}`;
  $('detail-algorithm').textContent = analysis.algorithm || '—';
  $('detail-summary').textContent = analysis.summary || '—';
  $('detail-complexity').textContent = analysis.complexity || '—';
  $('detail-clarity-summary').textContent = clarity.summary || '—';
  $('detail-neutral').textContent = clarity.neutral_statement || '—';
  const tags = $('detail-tags'); tags.replaceChildren();
  for (const tag of analysis.inferred_tags || []) {
    const chip = document.createElement('span'); chip.textContent = tag; tags.append(chip);
  }
  renderCriteria(clarity);
  const issues = [...(clarity.issues || []), ...(clarity.unresolved_ambiguities || []).map(value => `仅凭题面仍不明确：${value}`)];
  $('detail-issues-wrap').classList.toggle('hidden', issues.length === 0);
  const issueList = $('detail-issues'); issueList.replaceChildren();
  for (const issue of issues) {
    const row = document.createElement('li'); row.textContent = issue; issueList.append(row);
  }
  $('detail-rank').textContent = `第 ${report.target_rank ?? '—'} / ${report.total_problems ?? '—'} 位`;
  renderRanking(report);
  $('detail-judge-reason').textContent = `模型判断：${report.judge_reason || '未提供说明'}`;
  $('detail-calibration').textContent = `校准：${calibration.method || '—'}；梯度位置 ${report.relative_percentile ?? '—'}%；参考题顺序逆转率 ${Math.round((calibration.anchor_inversion_rate || 0) * 100)}%。`;
  $('detail-content').classList.remove('hidden');
}

async function loadDetail(id) {
  try {
    const response = await fetch(`/api/history/${encodeURIComponent(id)}`);
    if (response.status === 404) {
      await loadLiveDetail(id);
      return;
    }
    if (!response.ok) throw new Error('无法读取评测详情，请稍后重试。');
    renderDetail(await response.json());
  } catch (error) {
    showMessage('detail-error', error.message || '无法读取评测详情。');
  }
}

async function loadLiveDetail(id) {
  const response = await fetch(`/jobs/${encodeURIComponent(id)}`, { cache: 'no-store' });
  if (response.status === 404) throw new Error('这条记录不存在，或任务状态已在服务重启后清除。');
  if (!response.ok) throw new Error('无法读取任务进度。');
  const job = await response.json();
  if (job.status === 'completed') {
    loadDetail(id);
    return;
  }
  $('live-detail').classList.remove('hidden');
  $('live-detail-title').textContent = job.status === 'failed' ? '评测未完成' : '正在评测题目';
  $('live-detail-date').textContent = job.status === 'failed' ? (job.error || '任务失败') : (phaseLabels[job.phase] || '处理中');
  $('live-detail-phase').textContent = job.total > 0 ? `当前阶段：${job.completed} / ${job.total}` : '任务仍在服务器运行，刷新页面不会取消。';
  const base = { queued: 3, analyzing: 10, clarity: 18, searching: 27, collecting: 35, ranking: 80 };
  const progress = job.phase === 'collecting' ? 35 + 45 * job.completed / Math.max(1, job.total)
    : job.phase === 'ranking' ? 80 + 18 * job.completed / Math.max(1, job.total) : base[job.phase] || 0;
  $('live-detail-fill').style.width = `${Math.max(0, Math.min(100, progress))}%`;
  if (job.status === 'queued' || job.status === 'running') {
    setTimeout(() => loadLiveDetail(id).catch(() => {}), 4000);
  }
}

$('history-more').addEventListener('click', loadHistory);
$('detail-download').addEventListener('click', () => {
  if (!reportToDownload) return;
  const blob = new Blob([JSON.stringify(reportToDownload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = 'xcpc-problemjudger-report.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

const detailId = window.location.pathname.match(/^\/history\/([^/]+)$/)?.[1];
if (detailId) {
  $('history-list-view').classList.add('hidden');
  $('history-detail-view').classList.remove('hidden');
  loadDetail(decodeURIComponent(detailId));
} else {
  loadHistory();
  refreshLive();
  setInterval(refreshLive, 4000);
}
