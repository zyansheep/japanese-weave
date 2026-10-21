"""The statistics the simulated reader's experiments need, in the standard library: ranks, quantiles, logistic
regression and a normal tail."""
import math


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float('nan')


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return float('nan')
    at = q * (len(xs) - 1)
    low = int(at)
    high = min(low + 1, len(xs) - 1)
    return xs[low] + (xs[high] - xs[low]) * (at - low)


def ranks(xs):
    """Ranks from 1, ties sharing their mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(xs, ys):
    """Rank correlation; nan when either side is constant."""
    rx, ry = ranks(xs), ranks(ys)
    mx, my = mean(rx), mean(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx, syy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float('nan')


def normal_tail(z):
    """P(|Z| > z) for a standard normal Z."""
    return math.erfc(abs(z) / math.sqrt(2))


def _solve(a, b):
    """a x = b for a small symmetric positive matrix, by Gaussian elimination; None when singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def logistic(ys, xs, iterations=25):
    """Logistic regression of 0/1 outcomes ys on rows of covariates xs (an intercept is added), by Newton's method.
    (coefficients, standard errors), intercept first; None when it doesn't converge (separation)."""
    rows = [[1.0] + list(x) for x in xs]
    k = len(rows[0])
    share = min(max(mean(ys), 1e-3), 1 - 1e-3)
    beta = [logit(share)] + [0.0] * (k - 1)
    for _ in range(iterations):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for y, row in zip(ys, rows):
            eta = sum(b * x for b, x in zip(beta, row))
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, eta))))
            w = p * (1 - p)
            for i in range(k):
                grad[i] += (y - p) * row[i]
                wi = w * row[i]
                for j in range(i, k):
                    hess[i][j] += wi * row[j]
        for i in range(k):
            for j in range(i):
                hess[i][j] = hess[j][i]
        step = _solve(hess, grad)
        if step is None:
            return None
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    else:
        return None
    if max(abs(b) for b in beta) > 25:
        return None
    se = []
    for i in range(k):
        unit = [1.0 if j == i else 0.0 for j in range(k)]
        column = _solve(hess, unit)
        if column is None or column[i] <= 0:
            return None
        se.append(math.sqrt(column[i]))
    return beta, se
