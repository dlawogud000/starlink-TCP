#include <linux/kernel.h>
#include <linux/math64.h>
#include <linux/timekeeping.h>
#include <linux/tcp.h>

#include <net/sock.h>
#include <net/tcp.h>
#include <net/tcp_redhat.h>

#define LEO_USEC_PER_MSEC 1000ULL
#define LEO_USEC_PER_SEC  1000000ULL

static u64 leo_now_real_us(void)
{
	return div_u64(ktime_get_real_ns(), NSEC_PER_USEC);
}

static u64 leo_now_mono_us(void)
{
	return div_u64(ktime_get_ns(), NSEC_PER_USEC);
}

static u32 leo_ewma_u32(u32 old, u32 sample, u32 shift)
{
	if (!old)
		return sample;

	shift = clamp_t(u32, shift, 1, 8);
	return old - (old >> shift) + (sample >> shift);
}

static u64 leo_ewma_u64(u64 old, u64 sample, u32 shift)
{
	if (!old)
		return sample;

	shift = clamp_t(u32, shift, 1, 8);
	return old - (old >> shift) + (sample >> shift);
}

static bool tcp_leo_port_matches(const struct sock *sk)
{
	const struct inet_sock *inet = inet_sk(sk);
	const struct net *net = sock_net(sk);
	u16 sport;
	u16 dport;
	int i;

	sport = ntohs(READ_ONCE(inet->inet_sport));
	dport = ntohs(READ_ONCE(inet->inet_dport));

	for (i = 0; i < 4; i++) {
		u32 port;

		port = READ_ONCE(
			net->ipv4.sysctl_tcp_leo_dynamic_allow_ports[i]);

		/* 0 means unused slot. */
		if (!port)
			continue;

		if (sport == port || dport == port)
			return true;
	}

	return false;
}

/*
 * The existing periodic model should use the same clock base as the old code.
 * Replace this helper with the exact time source currently used by
 * tcp_leo_rwnd_phase_ms().
 */
static u64 leo_period_position_us(struct net *net, u64 now_us)
{
	u64 period_us;
	u64 offset_us;

	period_us = (u64)READ_ONCE(
		net->ipv4.sysctl_tcp_leo_rwnd_period_ms) *
		LEO_USEC_PER_MSEC;
	offset_us = (u64)READ_ONCE(
		net->ipv4.sysctl_tcp_leo_rwnd_offset_ms) *
		LEO_USEC_PER_MSEC;

	if (!period_us)
		return 0;

	return (now_us + period_us - (offset_us % period_us)) % period_us;
}

static u64 leo_period_id(struct net *net, u64 now_us)
{
	u64 period_us;
	u64 offset_us;

	period_us = (u64)READ_ONCE(
		net->ipv4.sysctl_tcp_leo_rwnd_period_ms) *
		LEO_USEC_PER_MSEC;
	offset_us = (u64)READ_ONCE(
		net->ipv4.sysctl_tcp_leo_rwnd_offset_ms) *
		LEO_USEC_PER_MSEC;

	if (!period_us)
		return 0;

	return div64_u64(now_us + period_us - (offset_us % period_us),
			 period_us);
}

static u32 leo_min_rwnd(struct sock *sk)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct inet_connection_sock *icsk = inet_csk(sk);
	struct net *net = sock_net(sk);
	u32 min_segs;
	u32 mss;
	u32 scale;

	min_segs = max_t(u32, 1,
		READ_ONCE(net->ipv4.sysctl_tcp_leo_rwnd_min_segs));

	mss = READ_ONCE(icsk->icsk_ack.rcv_mss);
	if (!mss)
		mss = READ_ONCE(tp->advmss);
	if (!mss)
		mss = TCP_MSS_DEFAULT;

	scale = 1U << tp->rx_opt.rcv_wscale;

	return ALIGN(min_segs * mss, scale);
}

static u32 leo_read_receiver_rtt_us(struct sock *sk)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct net *net = sock_net(sk);
	u32 scaled;
	u32 rtt_us;

	scaled = READ_ONCE(tp->rcv_rtt_est.rtt_us);
	rtt_us = scaled >> 3;

	if (!rtt_us) {
		rtt_us = max_t(u32, 1,
			READ_ONCE(
				net->ipv4.sysctl_tcp_leo_dynamic_fallback_rtt_ms)) *
			USEC_PER_MSEC;
	}

	return rtt_us;
}

static u64 leo_fallback_rate_Bps(struct net *net)
{
    u64 mbps;

    mbps = max_t(u32, 1,
        READ_ONCE(net->ipv4.sysctl_tcp_leo_dynamic_fallback_rate_mbps));

    /* Mbps -> bytes/s */
    return div64_u64(mbps * 1000000ULL, 8);
}

static void leo_update_measurements(struct sock *sk, u32 normal_win,
                    u64 now_us, enum tcp_leo_phase phase)
{
    struct tcp_sock *tp = tcp_sk(sk);
    struct tcp_leo_dynamic_state *st = &tp->leo_state;
    struct net *net = sock_net(sk);
    u32 sample_ms;
    u32 shift;
    u32 rtt_sample_us;
    u64 bytes_now;
    u64 delta_bytes;
    u64 delta_us;
    u64 rate_sample_Bps;

    sample_ms = max_t(u32, 1,
        READ_ONCE(net->ipv4.sysctl_tcp_leo_dynamic_sample_ms));
    shift = READ_ONCE(net->ipv4.sysctl_tcp_leo_dynamic_ewma_shift);

    if (!st->initialized) {
        memset(st, 0, sizeof(*st));
        st->last_sample_us = now_us;
        st->last_bytes_received = READ_ONCE(tp->bytes_received);
        st->normal_rwnd_ewma = normal_win;
        st->rtt_us = leo_read_receiver_rtt_us(sk);
        st->rtt_baseline_us = st->rtt_us;
        st->recv_rate_Bps_ewma = leo_fallback_rate_Bps(net);
        st->current_phase = TCP_LEO_NORMAL;
        st->initialized = 1;
        return;
    }

    delta_us = now_us - st->last_sample_us;
    if (delta_us < (u64)sample_ms * LEO_USEC_PER_MSEC)
        return;

    bytes_now = READ_ONCE(tp->bytes_received);
    delta_bytes = bytes_now - st->last_bytes_received;

    if (phase == TCP_LEO_NORMAL) {
        rate_sample_Bps = delta_bytes ? div64_u64(delta_bytes * LEO_USEC_PER_SEC, delta_us) : 0;
        st->recv_rate_Bps_ewma = leo_ewma_u64(
            st->recv_rate_Bps_ewma, rate_sample_Bps, shift);
    }

    rtt_sample_us = leo_read_receiver_rtt_us(sk);
    st->rtt_us = rtt_sample_us;

    if (phase == TCP_LEO_NORMAL) {
        st->rtt_baseline_us =
            leo_ewma_u32(st->rtt_baseline_us,
                    rtt_sample_us,
                    shift);
    }

    /*
     * Do not learn the window while our own controller is reducing it.
     * Otherwise the controlled rwnd becomes the next 'normal' rwnd.
     */
    if (phase == TCP_LEO_NORMAL)
        st->normal_rwnd_ewma = leo_ewma_u32(
            st->normal_rwnd_ewma, normal_win, shift);

    st->last_bytes_received = bytes_now;
    st->last_sample_us = now_us;
}

static u32 leo_calc_pre_us(struct net *net, u32 start_rwnd,
               u32 min_rwnd, u64 rate_Bps)
{
    u64 delta_bytes;
    u64 pre_us;
    u64 min_us;
    u64 max_us;

    min_us = (u64)max_t(u32, 1,
        READ_ONCE(net->ipv4.sysctl_tcp_leo_dynamic_pre_min_ms)) *
        LEO_USEC_PER_MSEC;
    max_us = (u64)max_t(u32, 1,
        READ_ONCE(net->ipv4.sysctl_tcp_leo_dynamic_pre_max_ms)) *
        LEO_USEC_PER_MSEC;

    if (start_rwnd <= min_rwnd || !rate_Bps)
        return (u32)min_us;

    delta_bytes = start_rwnd - min_rwnd;
    pre_us = div64_u64(delta_bytes * LEO_USEC_PER_SEC, rate_Bps);

    if (min_us > max_us) swap(min_us, max_us);
    return (u32)clamp_t(u64, pre_us, min_us, max_us);
}

static u32 leo_prediction_guard_us(struct net *net)
{
    return (u32)(
        (u64)READ_ONCE(
            net->ipv4.sysctl_tcp_leo_dynamic_prediction_guard_ms)
        * LEO_USEC_PER_MSEC
    );
}

static u32 leo_calc_drain_us(struct net *net, u32 rtt_us)
{
    u64 min_us;
    u64 max_us;
    u64 drain_us;

    min_us =
        (u64)READ_ONCE(
            net->ipv4.sysctl_tcp_leo_dynamic_drain_min_ms) *
        LEO_USEC_PER_MSEC;

    max_us =
        (u64)READ_ONCE(
            net->ipv4.sysctl_tcp_leo_dynamic_drain_max_ms) *
        LEO_USEC_PER_MSEC;

    if (min_us > max_us)
        swap(min_us, max_us);

    drain_us = rtt_us / 2;

    return (u32)clamp_t(
        u64,
        drain_us,
        min_us,
        max_us);
}

static u32 leo_calc_recovery_us(struct net *net, u32 rtt_us)
{
    u64 min_us;
    u64 max_us;
    u64 recovery_us;

    if (!READ_ONCE(
            net->ipv4.sysctl_tcp_leo_dynamic_recovery_enable))
        return 0;

    min_us =
        (u64)READ_ONCE(
            net->ipv4.sysctl_tcp_leo_dynamic_recovery_min_ms) *
        LEO_USEC_PER_MSEC;

    max_us =
        (u64)READ_ONCE(
            net->ipv4.sysctl_tcp_leo_dynamic_recovery_max_ms) *
        LEO_USEC_PER_MSEC;

    if (min_us > max_us)
        swap(min_us, max_us);

    /*
     * First experiment: ramp for RTT/2.
     * min/max sysctls allow sensitivity experiments.
     */
    recovery_us = rtt_us / 2;

    return (u32)clamp_t(
        u64,
        recovery_us,
        min_us,
        max_us);
}

static u32 leo_calc_outage_us(struct net *net,
                              u32 rtt_us,
                              u32 rtt_min_us)
{
    u64 outage_us;
    u64 min_us;
    u64 max_us;
    u64 guard_us;
    u64 theoretical_min;
    u64 prediction_guard_us;

    min_us =
        (u64)READ_ONCE(
            net->ipv4.sysctl_tcp_leo_dynamic_outage_min_ms) *
        LEO_USEC_PER_MSEC;

    max_us =
        (u64)READ_ONCE(
            net->ipv4.sysctl_tcp_leo_dynamic_outage_max_ms) *
        LEO_USEC_PER_MSEC;

    guard_us =
        (u64)READ_ONCE(
            net->ipv4.sysctl_tcp_leo_dynamic_outage_guard_ms) *
        LEO_USEC_PER_MSEC;
    prediction_guard_us = leo_prediction_guard_us(net);

    if (min_us > max_us)
        swap(min_us, max_us);

    if (rtt_us > rtt_min_us)
        theoretical_min =
            (rtt_us - rtt_min_us) / 2;
    else
        theoretical_min = 0;

    if(READ_ONCE(net->ipv4.sysctl_tcp_leo_dynamic_pred_guard_to_outage))
        outage_us =
            theoretical_min + guard_us + prediction_guard_us;
    else
        outage_us =
            theoretical_min + guard_us;

    outage_us = clamp_t(
        u64,
        outage_us,
        min_us,
        max_us + prediction_guard_us);

    /*
     * By design, receiver-side OUTAGE should end
     * no later than the physical handover boundary.
     */
    outage_us =
        min_t(u64, outage_us, rtt_us / 2);

    return (u32)outage_us;
}

static u64 leo_required_freeze_ahead_us(
	struct sock *sk,
	u32 normal_win)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct tcp_leo_dynamic_state *st = &tp->leo_state;
	struct net *net = sock_net(sk);
	u32 min_rwnd;
	u32 start_rwnd;
	u32 rtt_us;
	u32 pre_us;
	u64 rate_Bps;
	u64 guard_us;
    u64 drain_us;
    u64 prediction_guard_us;

	min_rwnd = leo_min_rwnd(sk);
    prediction_guard_us = leo_prediction_guard_us(net);

	start_rwnd = st->normal_rwnd_ewma ?
		st->normal_rwnd_ewma : normal_win;
	start_rwnd = min(start_rwnd, normal_win);
	start_rwnd = max(start_rwnd, min_rwnd);

	rtt_us = st->rtt_us ?
		st->rtt_us :
		leo_read_receiver_rtt_us(sk);

	rate_Bps = st->recv_rate_Bps_ewma ?
		st->recv_rate_Bps_ewma :
		leo_fallback_rate_Bps(net);

	pre_us = leo_calc_pre_us(net,
				 start_rwnd,
				 min_rwnd,
				 rate_Bps);

    drain_us = leo_calc_drain_us(net, rtt_us);

	guard_us =
		(u64)READ_ONCE(
			net->ipv4.sysctl_tcp_leo_dynamic_freeze_guard_ms) *
		LEO_USEC_PER_MSEC;

	return (u64)pre_us + rtt_us / 2 + drain_us + guard_us + prediction_guard_us;
}

static void leo_freeze_parameters(struct sock *sk, u32 normal_win,
                  u64 now_us)
{
    struct tcp_sock *tp = tcp_sk(sk);
    struct tcp_leo_dynamic_state *st = &tp->leo_state;
    struct net *net = sock_net(sk);
    u32 min_rwnd;
    u32 start_rwnd;
    u32 rtt_us;
    u64 rate_Bps;

    min_rwnd = leo_min_rwnd(sk);
    start_rwnd = st->normal_rwnd_ewma ? st->normal_rwnd_ewma : normal_win;
    start_rwnd = min(start_rwnd, normal_win);
    start_rwnd = max(start_rwnd, min_rwnd);

    rtt_us = st->rtt_us ? st->rtt_us :
        leo_read_receiver_rtt_us(sk);
    rate_Bps = st->recv_rate_Bps_ewma ? st->recv_rate_Bps_ewma :
        leo_fallback_rate_Bps(net);

    st->frozen_period_id = leo_period_id(net, now_us);
    st->frozen_rtt_us = rtt_us;
    st->frozen_rtt_half_us = rtt_us / 2;
    st->frozen_recv_rate_Bps = rate_Bps;
    st->frozen_start_rwnd = start_rwnd;
    st->frozen_min_rwnd = min_rwnd;
    st->frozen_pre_us = leo_calc_pre_us(net, start_rwnd, min_rwnd,
                        rate_Bps);
    st->frozen_outage_us = leo_calc_outage_us(net, rtt_us,
                          st->rtt_baseline_us);
    st->frozen_drain_us = leo_calc_drain_us(net, rtt_us);
    st->frozen_recovery_us = leo_calc_recovery_us(net, rtt_us);
    st->frozen_prediction_guard_us = leo_prediction_guard_us(net);
    st->pre_started = 0;
    st->recovery_started = 0;
    st->recovery_start_real_us = 0;
    st->frozen_pre_start_wnd = 0;
    st->frozen_pre_start_rcv_nxt = 0;
    st->frozen = 1;

    pr_info_ratelimited(
        "LEO freeze: rtt=%lluus half=%lluus rate=%lluBps "
        "rwnd=%llu min=%llu pre=%lluus "
        "drain=%lluus pred_guard=%lluus "
        "effective_drain=%lluus outage=%lluus recovery=%lluus\n",
        (unsigned long long)st->frozen_rtt_us,
        (unsigned long long)st->frozen_rtt_half_us,
        (unsigned long long)st->frozen_recv_rate_Bps,
        (unsigned long long)st->frozen_start_rwnd,
        (unsigned long long)st->frozen_min_rwnd,
        (unsigned long long)st->frozen_pre_us,
        (unsigned long long)st->frozen_drain_us,
        (unsigned long long)st->frozen_prediction_guard_us,
        (unsigned long long)(
            st->frozen_drain_us +
            st->frozen_prediction_guard_us),
        (unsigned long long)st->frozen_outage_us,
        (unsigned long long)st->frozen_recovery_us);
}

static void leo_maybe_freeze(struct sock *sk, u32 normal_win, u64 now_us)
{
    struct tcp_sock *tp = tcp_sk(sk);
    struct tcp_leo_dynamic_state *st = &tp->leo_state;
    struct net *net = sock_net(sk);
    u64 period_us;
    u64 pos_us;
    u64 until_handover_us;
    u64 freeze_ahead_us;

    period_us = (u64)READ_ONCE(
        net->ipv4.sysctl_tcp_leo_rwnd_period_ms) *
        LEO_USEC_PER_MSEC;
    if (!period_us)
        return;

    pos_us = leo_period_position_us(net, now_us);
    until_handover_us = period_us - pos_us;
    freeze_ahead_us = leo_required_freeze_ahead_us(sk, normal_win);

    /*
     * RECOVERY starts on the first callback after the predicted
     * handover boundary.  Keep the frozen snapshot across the boundary
     * until that recovery ramp has actually been visible to TCP.
     */
    if (st->frozen && st->recovery_started) {
        u64 recovery_elapsed_us =
            now_us - st->recovery_start_real_us;

        if (recovery_elapsed_us >= st->frozen_recovery_us)
            st->frozen = 0;
    }

    /*
     * If recovery is disabled, release the old snapshot once the
     * period boundary has passed.
     */
    if (st->frozen &&
        !st->frozen_recovery_us &&
        leo_period_id(net, now_us) != st->frozen_period_id)
        st->frozen = 0;

    if (!st->frozen && until_handover_us <= freeze_ahead_us)
        leo_freeze_parameters(sk, normal_win, now_us);
}

static enum tcp_leo_phase leo_phase(struct sock *sk, u64 now_us,
                    u64 *elapsed_us)
{
    struct tcp_sock *tp = tcp_sk(sk);
    struct tcp_leo_dynamic_state *st = &tp->leo_state;
    struct net *net = sock_net(sk);
    u64 period_us;
    u64 pos_us;
    u64 current_period_id;
    u64 pre_start_us;
    u64 outage_start_us;
    u64 outage_end_us;
    u64 drain_start_us;
    u64 prediction_guard_us;

    *elapsed_us = 0;

    if (!st->frozen)
        return TCP_LEO_NORMAL;

    period_us = (u64)READ_ONCE(
        net->ipv4.sysctl_tcp_leo_rwnd_period_ms) *
        LEO_USEC_PER_MSEC;
    if (!period_us)
        return TCP_LEO_NORMAL;

    pos_us = leo_period_position_us(net, now_us);
    current_period_id = leo_period_id(net, now_us);
    prediction_guard_us = st->frozen_prediction_guard_us;

    /*
     * Before the predicted handover boundary, use the original
     * scheduled PRE -> DRAIN -> OUTAGE timeline.
     */
    if (current_period_id == st->frozen_period_id) {
        outage_start_us =
            period_us > st->frozen_rtt_half_us ?
            period_us - st->frozen_rtt_half_us : 0;

        drain_start_us =
            outage_start_us >
                st->frozen_drain_us + prediction_guard_us ?
            outage_start_us -
                st->frozen_drain_us -
                prediction_guard_us : 0;

        pre_start_us =
            drain_start_us > st->frozen_pre_us ?
            drain_start_us - st->frozen_pre_us : 0;

        outage_end_us =
            outage_start_us + st->frozen_outage_us;

        if (pos_us >= pre_start_us &&
            pos_us < drain_start_us) {
            *elapsed_us = pos_us - pre_start_us;
            return TCP_LEO_PRE_HANDOVER;
        }

        if (pos_us >= drain_start_us &&
            pos_us < outage_start_us) {
            *elapsed_us = pos_us - drain_start_us;
            return TCP_LEO_DRAIN;
        }

        if (pos_us >= outage_start_us &&
            pos_us < min_t(u64, outage_end_us, period_us)) {
            *elapsed_us = pos_us - outage_start_us;
            return TCP_LEO_OUTAGE;
        }

        /*
         * Do not start recovery before predicted H even if the
         * configured OUTAGE is shorter than RTT/2.
         */
        return TCP_LEO_NORMAL;
    }

    /*
     * We are at/after the predicted handover boundary.
     * Usually there were no callbacks during the physical blackout.
     * Start RECOVERY on the first callback here, so blackout duration
     * does not consume the ramp.
     */
    if (current_period_id > st->frozen_period_id &&
        st->frozen_recovery_us) {

        if (!st->recovery_started) {
            st->recovery_started = 1;
            st->recovery_start_real_us = now_us;
            *elapsed_us = 0;
            return TCP_LEO_RECOVERY;
        }

        *elapsed_us =
            now_us - st->recovery_start_real_us;

        if (*elapsed_us < st->frozen_recovery_us)
            return TCP_LEO_RECOVERY;
    }

    return TCP_LEO_NORMAL;
}

static u32 leo_pre_target(
	const struct tcp_sock *tp,
	const struct tcp_leo_dynamic_state *st,
	u64 elapsed_us)
{
	u64 planned;
	u64 received;
	u64 reduction;
	u32 delta_wnd;

	if (!st->pre_started)
		return st->frozen_start_rwnd;

	if (st->frozen_pre_start_wnd <= st->frozen_min_rwnd)
		return st->frozen_min_rwnd;

	if (!st->frozen_pre_us)
		return st->frozen_pre_start_wnd;

	delta_wnd =
		st->frozen_pre_start_wnd -
		st->frozen_min_rwnd;

	planned = div64_u64(
		(u64)delta_wnd *
		min_t(u64, elapsed_us, st->frozen_pre_us),
		st->frozen_pre_us);

	received =
		(u32)(READ_ONCE(tp->rcv_nxt) -
		      st->frozen_pre_start_rcv_nxt);

	reduction = min(planned, received);
	reduction = min_t(u64, reduction, delta_wnd);

	return st->frozen_pre_start_wnd -
	       (u32)reduction;
}

static u32 leo_recovery_target(
    const struct tcp_leo_dynamic_state *st,
    u32 normal_win,
    u64 elapsed_us)
{
    u32 end_wnd;
    u32 delta_wnd;
    u64 increase;

    /*
     * Ramp from the controlled minimum back to the normal window
     * observed when the cycle was frozen.  normal_win remains the
     * hard upper bound imposed by ordinary TCP.
     */
    end_wnd = min(normal_win, st->frozen_start_rwnd);
    end_wnd = max(end_wnd, st->frozen_min_rwnd);

    if (!st->frozen_recovery_us ||
        end_wnd <= st->frozen_min_rwnd)
        return end_wnd;

    delta_wnd = end_wnd - st->frozen_min_rwnd;

    increase = div64_u64(
        (u64)delta_wnd *
        min_t(u64, elapsed_us, st->frozen_recovery_us),
        st->frozen_recovery_us);

    return st->frozen_min_rwnd + (u32)increase;
}

static u32 leo_safe_target(
    const struct tcp_sock *tp,
	const struct tcp_leo_dynamic_state *st,
	u32 desired)
{
	u32 received;
	u32 max_reduction;
	u32 safe_target;

	received = tp->rcv_nxt -
		   st->frozen_pre_start_rcv_nxt;

	max_reduction = min_t(u32,
			      received,
			      st->frozen_pre_start_wnd -
			      st->frozen_min_rwnd);

	safe_target = st->frozen_pre_start_wnd -
		      max_reduction;

	return max(desired, safe_target);
}

void tcp_leo_dynamic_init(struct sock *sk)
{
    struct tcp_sock *tp = tcp_sk(sk);

    memset(&tp->leo_state, 0, sizeof(tp->leo_state));
    tp->leo_state.current_phase = TCP_LEO_NORMAL;
}

u32 tcp_leo_dynamic_target(struct sock *sk, u32 normal_win)
{
    struct tcp_sock *tp = tcp_sk(sk);
    struct tcp_leo_dynamic_state *st = &tp->leo_state;
    struct net *net = sock_net(sk);
    enum tcp_leo_phase phase;
    enum tcp_leo_phase old_phase;
    u64 elapsed_us;
    u64 now_real_us;
    u64 now_mono_us;
    u32 target;

    if (!READ_ONCE(net->ipv4.sysctl_tcp_leo_rwnd_enable))
        return normal_win;

    if (!READ_ONCE(net->ipv4.sysctl_tcp_leo_dynamic_enable))
        return normal_win; /* Call old fixed controller instead if desired. */

    if (!tcp_leo_port_matches(sk))
		return normal_win;

    old_phase = st->current_phase;

    now_real_us = leo_now_real_us();
    now_mono_us = leo_now_mono_us();

    if (!st->initialized){
        leo_update_measurements(sk, normal_win, now_mono_us, TCP_LEO_NORMAL);
    }
    leo_maybe_freeze(sk, normal_win, now_real_us);
    phase = leo_phase(sk, now_real_us, &elapsed_us);
    if (old_phase != phase) {
        pr_info_ratelimited(
            "LEO phase %d -> %d, elapsed=%lluus\n",
            old_phase,
            phase,
            (unsigned long long)elapsed_us);
    }
    leo_update_measurements(sk, normal_win, now_mono_us, phase);
    if (phase == TCP_LEO_PRE_HANDOVER &&
        old_phase != TCP_LEO_PRE_HANDOVER) {
        st->frozen_pre_start_wnd =
            min(normal_win, st->frozen_start_rwnd);

        st->frozen_pre_start_wnd =
            max(st->frozen_pre_start_wnd,
                st->frozen_min_rwnd);

        st->frozen_pre_start_rcv_nxt =
            READ_ONCE(tp->rcv_nxt);

        st->pre_started = 1;
    }
    if ((phase == TCP_LEO_PRE_HANDOVER ||
        phase == TCP_LEO_OUTAGE ||
        phase == TCP_LEO_DRAIN) &&
        !st->pre_started) {
        st->frozen_pre_start_wnd =
            min(normal_win, st->frozen_start_rwnd);

        st->frozen_pre_start_wnd =
            max(st->frozen_pre_start_wnd,
                st->frozen_min_rwnd);

        st->frozen_pre_start_rcv_nxt =
            READ_ONCE(tp->rcv_nxt);

        st->pre_started = 1;
    }
    st->current_phase = phase;

    switch (phase) {
    case TCP_LEO_PRE_HANDOVER:
        target = leo_pre_target(tp, st, elapsed_us);
        break;
    case TCP_LEO_DRAIN:
        target = leo_safe_target(tp, st, st->frozen_min_rwnd);
        break;
    case TCP_LEO_OUTAGE:
        target = leo_safe_target(tp, st, st->frozen_min_rwnd);
        break;
    case TCP_LEO_RECOVERY:
        target = leo_recovery_target(st, normal_win, elapsed_us);
        break;

    case TCP_LEO_NORMAL:
    default:
        target = normal_win;
        break;
    }

    /* Never advertise more than ordinary TCP currently allows. */
    return min(target, normal_win);
}
