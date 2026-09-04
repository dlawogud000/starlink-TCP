#ifndef _NET_TCP_REDHAT_H
#define _NET_TCP_REDHAT_H

#include <linux/types.h>

struct sock;

enum tcp_leo_phase {
	TCP_LEO_NORMAL = 0,
	TCP_LEO_PRE_HANDOVER,
	TCP_LEO_DRAIN,
	TCP_LEO_OUTAGE,
	TCP_LEO_RECOVERY,
};

/*
 * Per-socket state. This structure is embedded in struct tcp_sock.
 * All time values use microseconds unless the field name ends with _ms.
 */
struct tcp_leo_dynamic_state {
	/* Measurement state */
	u64 last_sample_us;
	u64 last_bytes_received;
	u64 recv_rate_Bps_ewma;
	u32 rtt_us;
	u32 rtt_baseline_us;
	u32 normal_rwnd_ewma;


	/* Parameters frozen for one predicted handover */
	u64 frozen_period_id;
	u32 frozen_rtt_us;
	u32 frozen_rtt_half_us;
	u64 frozen_recv_rate_Bps;
	u32 frozen_start_rwnd;
	u32 frozen_min_rwnd;
	u32 frozen_pre_us;
	u32 frozen_outage_us;
	u32 frozen_drain_us;
	u32 frozen_recovery_us;
	u32 frozen_prediction_guard_us;


	u32 frozen_pre_start_wnd;
	u32 frozen_pre_start_rcv_nxt;
	u8 pre_started;
	u8 recovery_started;
	u64 recovery_start_real_us;

	/* Debug/current state */
	u8 initialized;
	u8 frozen;
	u8 current_phase;
};

void tcp_leo_dynamic_init(struct sock *sk);

/*
 * Called before selecting the advertised receive window.
 * normal_win is the window that ordinary TCP would advertise.
 */
u32 tcp_leo_dynamic_target(struct sock *sk, u32 normal_win);

#endif /* _TCP_LEO_DYNAMIC_H */
