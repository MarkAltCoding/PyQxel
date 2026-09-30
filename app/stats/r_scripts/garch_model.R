# GARCH(1,1) conditional volatility model.
#
# Sourced once into the embedded R session by app/stats/garch.py. Inputs arrive
# already cleaned (finite, de-gapped, in percent), so this file only fits and
# forecasts. Every value returned is a plain numeric/character vector so the
# Python side can read it without R-specific conversion.

suppressPackageStartupMessages(library(rugarch))

# Fit a constant-mean GARCH(1,1) to `returns` and forecast `horizon` steps ahead.
#
# returns       numeric vector of periodic returns in percent, oldest first.
# horizon       number of periods to forecast.
# distribution  innovation distribution: "norm" (Gaussian) or "std" (Student t).
#
# Returns a named list:
#   converged        logical; FALSE means the remaining fields are absent.
#   message          character; solver diagnostics when not converged.
#   coef_names, coef_values, coef_std_errors
#                    parameter estimates (mu, omega, alpha1, beta1[, shape])
#                    with robust (QML) standard errors.
#   sigma            in-sample conditional standard deviation per period.
#   forecast_sigma   forecast conditional standard deviation per period.
#   persistence      alpha1 + beta1.
#   unconditional_sigma  long-run standard deviation per period (NA if
#                    persistence >= 1).
#   log_likelihood, aic, bic   fit statistics (information criteria per obs).
pyqxel_fit_garch <- function(returns, horizon, distribution) {
  spec <- ugarchspec(
    variance.model = list(model = "sGARCH", garchOrder = c(1, 1)),
    mean.model = list(armaOrder = c(0, 0), include.mean = TRUE),
    distribution.model = distribution
  )

  # hybrid tries solnp, then nlminb, gosolnp and lbfgs until one converges.
  fit <- tryCatch(
    ugarchfit(spec, data = returns, solver = "hybrid"),
    error = function(e) e
  )
  if (inherits(fit, "error")) {
    return(list(converged = FALSE, message = conditionMessage(fit)))
  }
  if (convergence(fit) != 0) {
    return(list(converged = FALSE, message = "Optimizer did not converge."))
  }

  robust <- fit@fit$robust.matcoef
  forecast <- ugarchforecast(fit, n.ahead = horizon)
  persistence <- unname(persistence(fit))
  long_run_variance <- unname(uncvariance(fit))
  info <- infocriteria(fit)

  list(
    converged = TRUE,
    message = "",
    coef_names = rownames(robust),
    coef_values = unname(robust[, 1]),
    coef_std_errors = unname(robust[, 2]),
    sigma = as.numeric(sigma(fit)),
    forecast_sigma = as.numeric(sigma(forecast)),
    persistence = persistence,
    unconditional_sigma = if (persistence < 1) sqrt(long_run_variance) else NA_real_,
    log_likelihood = unname(likelihood(fit)),
    aic = unname(info["Akaike", 1]),
    bic = unname(info["Bayes", 1])
  )
}
