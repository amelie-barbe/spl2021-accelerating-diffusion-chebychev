import numpy as np
import matplotlib.pyplot as plt
from time import time
from tqdm import tqdm

# Usefull functions
from scipy.special import ive # Bessel function
from scipy.special import factorial
from scipy.spatial.distance import squareform
from scipy.linalg import expm

# Sparse matrix algebra
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import eigsh # Eigenvalues computation
from scipy.sparse.csgraph import laplacian
from scipy.sparse.linalg import expm_multiply as sparse_expm_multiply

import utils

# Logging. errors/warnings handling
from pdb import set_trace as bp
import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logging.basicConfig(level=logging.INFO,  format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger("matplotlib").setLevel(logging.WARNING) # Don't want matplotlib to print so much stuff
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG) # Max level of debug info. to display

################################################################################
### Utility functions ##########################################################
################################################################################

def plot_fancy_error_bar(x, y, ax=None, type="median_quartiles", label=None, **kwargs):
    """ Plot data with errorbars and semi-transparent error region.

    Arguments:
    x -- list or ndarray, shape (nx,)
        x-axis data
    y -- ndarray, shape (nx,ny)
        y-axis data. Usually represents ny attempts for each datum in x.
    ax -- matplotlib Axis
        Axis to plot the data on
    type -- string.
        Type of error. Either "median_quartiles" or "average_std".
    kwargs -- dict
        Extra options for matplotlib (such as color, label, etc).
    """
    if type=="median_quartiles":
        y_center    = np.percentile(y, q=50, axis=-1)
        y_up        = np.percentile(y, q=25, axis=-1)
        y_down      = np.percentile(y, q=75, axis=-1)
    elif type=="average_std":
        y_center    = np.average(x, axis=-1)
        y_std       = np.std(x, axis=-1)
        y_up        = y_center + y_std
        y_down      = y_center - y_std

    if ax is None:
        plot_ = plt.errorbar(x, y_center, (y_center - y_down, y_up - y_center), label=label, **kwargs)
        plt.fill_between(x, y_down, y_up, alpha=.3, **kwargs)
    else:
        plot_ = ax.errorbar(x, y_center, (y_center - y_down, y_up - y_center), label=label, **kwargs)
        ax.fill_between(x, y_down, y_up, alpha=.3, **kwargs)
    return plot_

################################################################################
### Krylov subspaces-base method from 1812.10165 (ART) #########################
################################################################################

def art_expm(A, v, t, toler=1e05, m=10, verbose=False):
    """ Computes y = exp(-t.A).v approximately.

    Uses the Arnoldi method with the RT (residual time) restarting proposed in
    M.A. Botchev, L.A. Knizhnerman, ART: adaptive residual-time restarting for
    Krylov subspace matrix exponential evaluations http://arxiv.org/abs/1812.10165

    Adapted from the MatLab implementation.

    Copyright (c) 2018 by M.A. Botchev
    Permission to copy all or part of this work is granted, provided that the
    copies are not made or distributed for resale, and that the copyright notice
    and this notice are retained.
    THIS WORK IS PROVIDED ON AN "AS IS" BASIS.  THE AUTHOR PROVIDES NO WARRANTY
    WHATSOEVER, EITHER EXPRESSED OR IMPLIED, REGARDING THE WORK, INCLUDING
    WARRANTIES WITH RESPECT TO ITS MERCHANTABILITY OR FITNESS FOR ANY
    PARTICULAR PURPOSE.

    Input:
    - A       (n x n)-matrix
    - v       n-vector
    - t>0     length of the time interval
    - toler>0 tolerance
    - m       maximal Krylov dimension
    Output:
    - y       the approximate solution
    - mvec    number of matrix-vector multiplications done to compute y (not anymore)
    """
    n = len(v)
    V = np.zeros((n,m+1))
    H = np.zeros((m+1,m))

    convergence = False
    mvec_count  = 0
    while not convergence:
        beta = np.linalg.norm(v)
        V[:,0] = np.squeeze(v/beta)

        for j in range(m):
            w = A@V[:,j]
            mvec_count = mvec_count + 1
            for i in range(j):
                H[i,j] = w.T @ V[:,i]
                w      = w - H[i,j]*V[:,i]
            H[j+1,j] = np.linalg.norm(w)
            e1       = np.zeros((j+1,1)); e1[0]  = 1
            ej       = np.zeros((j+1,1)); ej[-1] = 1
            s        =  [t*i/6 for i in range(6)]
            beta_j   = np.empty( (len(s),), dtype=np.float )
            for q in range(len(s)):
                u         = expm(-s[q] * H[0:j+1,0:j+1]) @ e1 # TODO: faster
                beta_j[q] = -H[j+1,j] * (ej.T @ u)
            resnorm = np.linalg.norm(beta_j, np.inf)
            if resnorm<=toler:
                if verbose: print(f"j = {j}, resnorm = {resnorm:.2e} - convergence!")
                convergence = True
                break
            elif j+1==m:
                if verbose: print("j = {j}, resnorm = {resnorm:.2e}")
                if verbose: print(f"-------- restart after {m} steps")
                # Find n_tsteps - number of steps to monitor the residual
                n_tsteps    = 100
                u           = e1
                resid_value = 2*toler
                while resid_value>toler:
                    expmH       = expm( -(t/n_tsteps) * H[0:j+1,0:j+1] )
                    u           = expmH @ e1
                    resid_value = -H[j+1,j] * (ej.T @ u)
                    if abs(resid_value)<=toler:
                        u = e1
                        break
                    n_tsteps = 2*n_tsteps
                # keyboard % to plot residual vs t - MB
                # Compute residual for intermediate time points until its
                # value exceeds tolerance
                for k in range(n_tsteps):
                    u_old       = u
                    u           = expmH @ u
                    resid_value = -H[j+1,j] * (ej.T @ u)
                    if abs(resid_value)>toler:
                        u_ok  = u_old
                        t_ok  = (k-1)/n_tsteps * t
                        y_ok  = V[:,0:j+1] @ (beta * u_ok)
                        if verbose: print(", time interval reduced by {round(t_ok/t*100)}%%")
                        t = t - t_ok
                        v = y_ok
                        break          # restart
                break                  # restart
            V[:,j+1] = w / H[j+1,j]

    y = V[:,0:j+1] @ (beta*u)

    # return y, mvec
    return y

################################################################################
### Our method to compute the diffusion ########################################
################################################################################

def compute_chebychev_pol(X, L, phi, K):
    """ Compute the Tk(L).X, where Tk are the K+1 first Chebychev polynoms. """
    N, d = X.shape
    T = np.empty((K + 1, N, d), dtype=np.float)
    # Initialisation
    T[0] = X
    T[1] = (1 / phi) * L @ X - T[0]
    # Calcul récursif de T[2], T[3], etc.
    for j in range(2, K + 1):
        T[j] = (2 / phi) * L @ T[j-1] - 2 * T[j-1] - T[j-2]
    return T

def compute_chebychev_coeff(n, tau, phi):
    """ Compute any Chebychev coefficient as a Bessel function"""
    return 2 * ive(n, -tau * phi)

def compute_chebychev_coeff_all(phi, tau, K):
    """ Compute recursively the K+1 Chebychev coefficients for our functions. """
    coeff = np.empty((K+1,), dtype=np.float)
    coeff[-1] = compute_chebychev_coeff(K, phi, tau)
    coeff[-2] = compute_chebychev_coeff(K-1, phi, tau)
    for i in range(K - 2, -1, -1):
        coeff[i] = coeff[i+2] - (2 * i + 2) / (tau * phi) * coeff[i+1]
    return coeff

def expm_multiply(L, X, tau, K=None):
    """ Computes the action of exp(-t*L) on X for all t in X."""
    # If K is not provided, fall back on default value (40).
    if K is None:
        K = K_base
    # Compute phi = l_max/2
    phi = eigsh(L, k=1, return_eigenvectors=False)[0] / 2
    # Compute Chebychev polynomials
    poly = compute_chebychev_pol(X, L, phi, K)
    # Check if tau is a simgle value or an array.
    if isinstance(tau, (float, int)):
        # Only 1 value.
        coeff = compute_chebychev_coeff_all(phi, tau, K)
        Y = .5 * coeff[0] * poly[0] + (poly[1:].T @ coeff[1:]).T
        return Y
    elif isinstance(tau, list):
        coeff_list = [compute_chebychev_coeff_all(phi, t, K) for t in tau]
        Y_list = [.5 * coeff[0] * poly[0] + (poly[1:].T @ coeff[1:]).T for coeff in coeff_list]
        return Y_list
    elif isinstance(tau, np.ndarray):
        f = lambda t: compute_chebychev_coeff_all(phi, t, K)
        g = lambda coeff: .5 * coeff[0] * poly[0] + (poly[1:].T @ coeff[1:]).T
        h = lambda t: g(f(t))
        # Yes I know, it' s afor loop.
        # I can't make np.vectorize work >.<
        out = np.empty(tau.shape+X.shape, dtype=X.dtype)
        for index,t in np.ndenumerate(tau):
            out[index] = h(t)
        return out
        # return np.vectorize(h)(tau)
    else:
        print(f"expm_multiply(): unsupported data type for tau ({type(tau)})")

def get_diffusion_fun(L, X, K=None):
    """ Creates a function to compute exp(-t*L) on X, for t given later."""
    # If K is not provided, fall back on default value.
    if K is None:
        K = K_base
    # Compute phi = l_max/2
    phi = eigsh(L, k=1, return_eigenvectors=False)[0] / 2
    # Compute Chebychev polynomials
    poly = compute_chebychev_pol(X, L, phi, K)
    # Define a function to be applied for multipel values of tau
    def f(tau):
        coeff = compute_chebychev_coeff_all(phi, tau, K)
        Y = .5 * coeff[0] * poly[0] + (poly[1:].T @ coeff[1:]).T
        return Y
    return f

################################################################################
### Data #######################################################################
################################################################################

def sample_er(N, p, gamma):
    """ Sample an Erdos-Reyni graph (as a laplacian) and a 1d gaussian signal on
        its nodes. """
    # Sample the adjacency matrix, in a compressed fashio (only generates the
    # top triangular part, as a 1-dimensional vector).
    A_compressed = np.random.choice(2, size=(N*(N-1)//2,), p=[1.-p,p])
    # Compute the graph's combinatorial laplacian
    L = laplacian(csr_matrix(squareform(A_compressed), dtype=np.float))
    # Sample the features
    X = np.random.randn(N,1) * gamma
    # Conclude
    return L, X

def get_er(k, N=200, p=.05, gamma=1.):
    """ Iterator. Yields k Erdos-Reyni graphs. """
    for i in range(k):
        yield sample_er(N, p, gamma)

#TODO: write self-sufficient function (with an appropriate wget?)
def get_firstmm_db(k):
    """ Iterator. Yields k attributed graphs from the FIRSTMM_DB dataset. """
    data_dict = utils.parse_dortmund_format(f"data/FIRSTMM_DB/", "FIRSTMM_DB")
    N = len(data_dict["node_attributes"])
    p = np.random.permutation(N)
    X_all = data_dict["node_attributes"][p[:k]]
    A_all = data_dict["graph_structures"][p[:k]]
    L_all = [laplacian(A) for A in A_all]
    return zip(L_all, X_all)

################################################################################
### Analysis of the theoretical bound ##########################################
################################################################################

def f_aux(C, K):
    """ Function f() defined in the paper (section on bound). """
    x = -2*C+C*C+(K+1)*np.log(C)-(K+.5)*np.log(K)+K
    return 2/(np.sqrt(2*np.pi)*(K+1-C))*np.exp(x)
    # return np.exp(C**2.) * 2 * np.exp(-2*C) * (C ** (K+1)) / (factorial(K) * (K+1-C))

def get_bound_1(L, x, tau, K):
    """ First bound of the paper. """
    phi = eigsh(L, k=1, return_eigenvectors=False)[0] / 2
    C   = tau*phi/2.
    a   = np.abs(np.sum(x))
    return (f_aux(C,K)*np.linalg.norm(x)/a)**2.

def get_bound_2(L, tau, K):
    """ Second bound of the paper. """
    phi = eigsh(L, k=1, return_eigenvectors=False)[0] / 2
    C   = tau*phi/2.
    return f_aux(C, K)**2. / np.exp(-8*C)

def E(C, K):
    b = 2 / (1 + np.sqrt(5))
    d = np.exp(b) / (2 + np.sqrt(5))
    if K <= C:
        return np.exp( -b * (K+1)**2. / (4*C)) * (1 + np.sqrt(C * np.pi / b)) + (d**(4*C)) / (1+b)
    else:
        return (d**K) / (1-d)

def get_bound_bergamaschi(L, tau, K):
    phi = eigsh(L, k=1, return_eigenvectors=False)[0] / 2
    C   = tau*phi/2.
    return (2*E(C, K)/np.exp(-4*C))**2.

def bound_analysis_firstmm_db():
    """ Display the average MSE and bounds for various graphs from the
        FIRSTMM_DB dataset, for various values of tau. """
    logger.debug("bound_analysis()")

    n_graphs = 10
    n_tau    = 20
    tau_all  = 10**np.linspace(-2.,0.,num=n_tau)
    K        = 10
    bound_1_all = np.empty( (n_graphs,n_tau), dtype=np.float )
    bound_2_all = np.empty( (n_graphs,n_tau), dtype=np.float )
    bound_b_all = np.empty( (n_graphs,n_tau), dtype=np.float )
    mse_all     = np.empty( (n_graphs,n_tau), dtype=np.float )

    # Compute bounds and errors
    for i,(L,X) in enumerate(get_er(n_graphs)):
        for j,tau in enumerate(tau_all):
            bound_1_all[i,j] = get_bound_1(L, X, tau, K)
            bound_2_all[i,j] = get_bound_2(L, tau, K)
            bound_b_all[i,j] = get_bound_bergamaschi(L, tau, K)
            y_ref = sparse_expm_multiply(-tau*L, X)
            y_apr = expm_multiply(L, X, tau, K)
            mse_all[i,j] = (np.linalg.norm(y_ref-y_apr)/np.linalg.norm(y_ref))**2
    bound_1_all = np.average(bound_1_all, axis=0)
    bound_2_all = np.average(bound_2_all, axis=0)
    bound_b_all = np.average(bound_b_all, axis=0)
    mse_all = np.average(mse_all, axis=0)

    # # Load data from FIRSTMM_DB dataset
    # d = utils.parse_dortmund_format("data/FIRSTMM_DB/", "FIRSTMM_DB", clean_data=True)
    # n = len(d["node_attributes"])
    #
    # logger.debug("Preparing data.")
    # p = np.argsort(d["graph_labels"])
    # p = np.random.default_rng().permutation(n)
    # L = d["node_labels"][p]
    # X_attr = d["node_attributes"][p]
    # # X_coor = d["node_coordinates"][p]
    # # X_norm = d["node_normals"][p]
    # M = d["graph_structures"][p]
    #
    # tau_all = 10**np.linspace(-2.,0.,num=20)
    # K = 10
    # laplac = laplacian(M[0])
    # signal = X_attr[0]
    #
    # logger.debug(f"Computing bounds for tau {tau_all[0]:.2f}-->{tau_all[-1]:.2f}")
    # bound_1_all = np.array([get_bound_1(laplac, signal, tau, K) for tau in tau_all])
    # bound_2_all = np.array([get_bound_2(laplac, tau, K) for tau in tau_all])
    #
    # # Compute real MSE for various tau
    # y_ref_all = [sparse_expm_multiply(-tau*laplac, signal) for tau in tau_all]
    # y_apr_all = [expm_multiply(laplac, signal, tau, K) for tau in tau_all]
    # mse_all   = [(np.linalg.norm(y_ref-y_apr)/np.linalg.norm(y_ref))**2. for y_ref,y_apr in zip(y_ref_all, y_apr_all)]

    # Plot all this
    plt.plot(tau_all, bound_1_all, label=f"Bound 1 (K={K})")
    plt.plot(tau_all, bound_2_all, label=f"Bound 2 (K={K})")
    plt.plot(tau_all, bound_b_all, label=f"Bergamaschi's (K={K})")
    plt.plot(tau_all, mse_all, label=f"MSE")
    plt.xlabel(r"$\tau$")
    plt.ylabel("Error")
    plt.xscale("log")
    plt.yscale("log")
    plt.grid()
    plt.legend()
    plt.show()

################################################################################
### Speed test on fixed tau ####################################################
################################################################################

def speed_analysis_er():
    n_graphs = 10
    rep_all  = list(range(1,6)) # [1] + [2*(i+1) for i in range(5)]
    n_rep    = len(rep_all)
    tau      = .1

    print("=== Erdos-Reyni + gaussian signal")
    time_sp = np.zeros((n_rep,n_graphs))
    time_ar = np.zeros((n_rep,n_graphs))
    time_cb = np.zeros((n_rep,n_graphs))

    pbar = tqdm(total=n_graphs*n_rep)
    for i,(L,X) in enumerate(get_er(n_graphs, N=10000, p=.05)):
        for j,rep in enumerate(rep_all):
            # Compute scipy's method
            t_start = time()
            for _ in range(rep):
                _ = sparse_expm_multiply(-tau*L, X)
            t_stop = time()
            time_sp[j,i] += t_stop - t_start
            # Compute ART's method
            t_start = time()
            for _ in range(rep):
                _ = art_expm(L, X, tau, toler=1e-5, m=60)
            t_stop = time()
            time_ar[j,i] += t_stop - t_start
            # Compute our method
            t_start = time()
            f = get_diffusion_fun(L, X, K=10)
            for _ in range(rep):
                _ = f(tau)
            t_stop = time()
            time_cb[j,i] += t_stop - t_start

            pbar.update(1)
    pbar.close()

    plot_fancy_error_bar(rep_all, time_sp, label="Scipy")
    plot_fancy_error_bar(rep_all, time_cb, label="Chebychev")
    plot_fancy_error_bar(rep_all, time_ar, label="ART (Krylov)")

    plt.xlabel("Number of repetitions")
    plt.ylabel("Time (s)")
    plt.legend()
    plt.grid()
    plt.show()

def speed_analysis_firstmm_db():
    n_graphs = 10
    rep_all  = [1] + [2*(i+1) for i in range(5)]
    n_rep    = len(rep_all)
    tau      = .1

    print("=== FIRSTMM_DB dataset")
    time_sp = np.zeros((n_graphs,n_rep))
    time_ar = np.zeros((n_graphs,n_rep))
    time_cb = np.zeros((n_graphs,n_rep))

    pbar = tqdm(total=n_graphs*n_rep)
    for i,(L,X) in enumerate(get_firstmm_db(n_graphs)):
        for j,rep in enumerate(rep_all):
            # Compute scipy's method
            t_start = time()
            for _ in range(rep):
                _ = sparse_expm_multiply(-tau*L, X)
            t_stop = time()
            time_sp[i,j] += t_stop - t_start
            # Compute ART's method
            t_start = time()
            for _ in range(rep):
                _ = art_expm(L, X, tau, toler=1e-3, m=60)
            t_stop = time()
            time_ar[i,j] += t_stop - t_start
            # Compute our method
            t_start = time()
            f = get_diffusion_fun(L, X, K=10)
            for _ in range(rep):
                _ = f(tau)
            t_stop = time()
            time_cb[i,j] += t_stop - t_start

            pbar.update(1)
    pbar.close()

    time_sp = time_sp / n_graphs
    time_ar = time_ar / n_graphs
    time_cb = time_cb / n_graphs

    time_sp = np.average(time_sp, axis=0)
    time_ar = np.average(time_ar, axis=0)
    time_cb = np.average(time_cb, axis=0)

    plt.plot(rep_all, time_sp, label="Scipy")
    plt.plot(rep_all, time_cb, label="Chebychev")
    plt.plot(rep_all, time_ar, label="ART (Krylov)")
    plt.xlabel("Number of repetitions")
    plt.ylabel("Time (s)")
    plt.legend()
    plt.grid()
    plt.show()

################################################################################
### Speed and precision with tau increasing ####################################
################################################################################

def speed_MSE_analysis_firstmm_db():
    # Experiment parameters
    n_graphs  = 10 # Number of graphs to average the performance over
    n_tau_val = 20 # Number of tau values
    n_runs    = 50 # Number of runs to average performances over
    tau_list  = 10**np.linspace(-5.,-1., num=n_tau_val)

    # How much time does each method takes
    time_sp = np.zeros((n_tau_val,n_runs,n_graphs))
    time_ar = np.zeros((n_tau_val,n_runs,n_graphs))
    time_cb = np.zeros((n_tau_val,n_runs,n_graphs))

    # Precision of the methods wrt to NumPy's (which we assum is correct up to arithmetic precision)
    err_ar = np.zeros((n_tau_val,n_runs,n_graphs))
    err_cb = np.zeros((n_tau_val,n_runs,n_graphs))

    # Loop over graphs
    pbar = tqdm(total=n_graphs*n_tau_val*n_runs)
    for i,(L,_) in enumerate(get_firstmm_db(n_graphs)):
        # Loop over runs
        for j in range(n_runs):
            # Build a standard signal/initial heat value: 1 on a node, 0 elswhere
            N,_ = L.shape
            idx = np.random.default_rng().integers(low=0,high=N)
            X = np.zeros((N,1), dtype=np.float)
            X[idx] = 1.
            # Pre-compute the Chebychev polynomials, and spread it the
            # computation time over all values of tau.
            t_start = time()
            f_cb = get_diffusion_fun(L, X, K=10)
            t_stop = time()
            time_cb[:,j,i] += (t_stop - t_start) / n_tau_val
            for k,tau in enumerate(tau_list):
                # Compute diffusion with scipy's method
                t_start = time()
                Y_sp = sparse_expm_multiply(-tau*L, X)
                t_stop = time()
                time_sp[k,j,i] += t_stop - t_start

                # Compute diffusion with ART's method
                t_start = time()
                Y_ar = art_expm(L, X, tau, toler=1e-3, m=20)
                t_stop = time()
                time_ar[k,j,i] += t_stop - t_start

                # Compute diffusion with our method
                t_start = time()
                Y_cb = f_cb(tau)
                t_stop = time()
                time_cb[k,j,i] += t_stop - t_start

                # Compute and store MSE
                err_ar[k,j,i] = (np.linalg.norm(Y_sp-Y_ar)/np.linalg.norm(Y_sp))**2
                err_cb[k,j,i] = (np.linalg.norm(Y_sp-Y_cb)/np.linalg.norm(Y_sp))**2

                pbar.update(1)
    pbar.close()

    # Average times/MSE over graphs
    time_sp = np.average(time_sp, axis=-1)
    time_ar = np.average(time_ar, axis=-1)
    time_cb = np.average(time_cb, axis=-1)
    err_ar = np.average(err_ar, axis=-1)
    err_cb = np.average(err_cb, axis=-1)

    # Prepare plots
    f, ax0 = plt.subplots(nrows=1, ncols=1)
    ax1 = plt.twinx()

    # Plot computation times wrt tau
    plt0sp = plot_fancy_error_bar(tau_list, time_sp, ax=ax0, color="red",   linestyle="solid", label="(time) Scipy")
    plt0cb = plot_fancy_error_bar(tau_list, time_cb, ax=ax0, color="blue",  linestyle="solid", label="(time) Chebychev")
    plt0ar = plot_fancy_error_bar(tau_list, time_ar, ax=ax0, color="green", linestyle="solid", label="(time) ART (Krylov)")

    # Plot MSE wrt tau
    plt1cb = plot_fancy_error_bar(tau_list, err_cb, ax=ax1, color="blue",  linestyle="dashed", label="(error) Chebychev")
    plt1ar = plot_fancy_error_bar(tau_list, err_ar, ax=ax1, color="green", linestyle="dashed", label="(error) ART (Krylov)")

    # Configure plot
    plt.xlabel(r"$\tau$")
    plt.xscale("log")
    ax0.set_ylabel("Time (s)")
    ax1.set_ylabel("MSE")
    ax1.set_yscale("log")
    plt_all = [plt0sp, plt0cb, plt0ar]
    plt.legend(plt_all, [plt_.get_label() for plt_ in plt_all])
    plt.grid()
    plt.show()

################################################################################
### Main #######################################################################
################################################################################

if __name__=="__main__":
    speed_MSE_analysis_firstmm_db()
    # bound_analysis_firstmm_db()
    # speed_analysis_er()
    # speed_analysis_firstmm_db()