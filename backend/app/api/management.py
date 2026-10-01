import csv
import io
import json
from datetime import datetime, timedelta, time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_

from ..database import get_db
from ..models.user import User, UserStore
from ..models.store import Store, ProductStoreListing
from ..models.product import Product
from ..models.makro_piggyback import MakroPiggybackItem
from ..models.task import TaskLog
from ..utils.auth import get_current_user, get_current_admin

router = APIRouter(prefix="/management", tags=["管理驾驶舱与员工工作量追踪"])

def _parse_date_range(start_date: Optional[str], end_date: Optional[str]):
    """解析日期范围并转换为精确的 datetime 起止点"""
    now = datetime.now()
    if not start_date and not end_date:
        # 默认今日全天
        start_dt = datetime.combine(now.date(), time.min)
        end_dt = datetime.combine(now.date(), time.max)
        prev_start_dt = start_dt - timedelta(days=1)
        prev_end_dt = datetime.combine((now - timedelta(days=1)).date(), time.max)
    else:
        try:
            s_d = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else now.date()
        except Exception:
            s_d = now.date()
        try:
            e_d = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else s_d
        except Exception:
            e_d = s_d
        
        start_dt = datetime.combine(s_d, time.min)
        end_dt = datetime.combine(e_d, time.max)
        delta_days = (e_d - s_d).days + 1
        prev_start_dt = start_dt - timedelta(days=delta_days)
        prev_end_dt = end_dt - timedelta(days=delta_days)

    return start_dt, end_dt, prev_start_dt, prev_end_dt


@router.get("/overview", summary="管理驾驶舱：核心指标宏观看板")
def get_management_overview(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    user_id: Optional[int] = Query(None, description="筛选特定员工ID"),
    store_id: Optional[int] = Query(None, description="筛选特定店铺ID"),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    start_dt, end_dt, prev_start_dt, prev_end_dt = _parse_date_range(start_date, end_date)

    # 1. 选品采集量 (Takealot 选品录入数)
    sourcing_q = db.query(Product).filter(Product.created_at.between(start_dt, end_dt))
    if user_id:
        sourcing_q = sourcing_q.filter(Product.user_id == user_id)
    sourcing_count = sourcing_q.count()

    prev_sourcing_q = db.query(Product).filter(Product.created_at.between(prev_start_dt, prev_end_dt))
    if user_id:
        prev_sourcing_q = prev_sourcing_q.filter(Product.user_id == user_id)
    prev_sourcing_count = prev_sourcing_q.count()

    # 2. AI 清洗量 (TaskLog 中 CLEAN 与 BATCH_CLEAN 记录)
    clean_q = db.query(TaskLog).filter(
        TaskLog.created_at.between(start_dt, end_dt),
        TaskLog.task_type.in_(["CLEAN", "BATCH_CLEAN"])
    )
    if user_id:
        clean_q = clean_q.filter(TaskLog.user_id == user_id)
    cleaning_count = clean_q.count()

    # 3. 侵权与合规检测量
    comp_q = db.query(TaskLog).filter(
        TaskLog.created_at.between(start_dt, end_dt),
        TaskLog.task_type.in_(["COMPLIANCE", "BATCH_COMPLIANCE", "PIGGYBACK_COMPLIANCE"])
    )
    if user_id:
        comp_q = comp_q.filter(TaskLog.user_id == user_id)
    compliance_count = comp_q.count()

    # 4. 店铺刊登发布统计
    pub_listing_q = db.query(ProductStoreListing).filter(ProductStoreListing.submitted_at.between(start_dt, end_dt))
    if user_id:
        pub_listing_q = pub_listing_q.filter(ProductStoreListing.user_id == user_id)
    if store_id:
        pub_listing_q = pub_listing_q.filter(ProductStoreListing.store_id == store_id)
    
    total_publishing = pub_listing_q.count()
    pub_success = pub_listing_q.filter(ProductStoreListing.status.in_(["SUBMITTED", "ACTIVE"])).count()
    pub_failed = pub_listing_q.filter(ProductStoreListing.status == "FAILED").count()
    pub_success_rate = round((pub_success / total_publishing * 100), 1) if total_publishing > 0 else 100.0

    # 如果没有 listing 提交记录，回退从 TaskLog 查询 SUBMIT_LISTING / BATCH_PUBLISH 统计
    if total_publishing == 0:
        pub_task_q = db.query(TaskLog).filter(
            TaskLog.created_at.between(start_dt, end_dt),
            TaskLog.task_type.in_(["SUBMIT_LISTING", "BATCH_PUBLISH"])
        )
        if user_id:
            pub_task_q = pub_task_q.filter(TaskLog.user_id == user_id)
        total_publishing = pub_task_q.count()
        pub_success = pub_task_q.filter(TaskLog.status == "SUCCESS").count()
        pub_failed = pub_task_q.filter(TaskLog.status == "FAILED").count()
        pub_success_rate = round((pub_success / total_publishing * 100), 1) if total_publishing > 0 else 100.0

    # 5. Makro 站内跟品统计
    piggy_q = db.query(MakroPiggybackItem).filter(MakroPiggybackItem.created_at.between(start_dt, end_dt))
    if user_id:
        piggy_q = piggy_q.filter(MakroPiggybackItem.user_id == user_id)
    if store_id:
        piggy_q = piggy_q.filter(MakroPiggybackItem.store_id == store_id)
    piggy_collected = piggy_q.count()
    piggy_published = piggy_q.filter(MakroPiggybackItem.status == "ACTIVE").count()
    piggy_winning = piggy_q.filter(MakroPiggybackItem.buybox_status == "WINNING").count()

    # 6. 全系统总操作流水与人效
    total_log_q = db.query(TaskLog).filter(TaskLog.created_at.between(start_dt, end_dt))
    if user_id:
        total_log_q = total_log_q.filter(TaskLog.user_id == user_id)
    total_actions = total_log_q.count()

    # 活跃员工数
    active_users_raw = (
        db.query(TaskLog.user_id)
        .filter(TaskLog.created_at.between(start_dt, end_dt), TaskLog.user_id != None)
        .distinct()
        .all()
    )
    active_user_ids = {u[0] for u in active_users_raw if u[0]}
    
    # 也把创建商品的员工算入活跃
    active_prod_users = (
        db.query(Product.user_id)
        .filter(Product.created_at.between(start_dt, end_dt), Product.user_id != None)
        .distinct()
        .all()
    )
    for u in active_prod_users:
        if u[0]:
            active_user_ids.add(u[0])

    active_operators_count = len(active_user_ids)
    all_operators_count = db.query(User).filter(User.is_active == True).count()
    per_capita_output = round(total_actions / max(1, active_operators_count), 1) if active_operators_count > 0 else 0

    # 环比增长计算
    sourcing_growth = 0.0
    if prev_sourcing_count > 0:
        sourcing_growth = round(((sourcing_count - prev_sourcing_count) / prev_sourcing_count) * 100, 1)

    return {
        "date_range": {
            "start": start_dt.strftime("%Y-%m-%d"),
            "end": end_dt.strftime("%Y-%m-%d")
        },
        "sourcing": {
            "total": sourcing_count,
            "prev_total": prev_sourcing_count,
            "growth_rate": sourcing_growth
        },
        "cleaning": {
            "total": cleaning_count
        },
        "compliance": {
            "total": compliance_count
        },
        "publishing": {
            "total": total_publishing,
            "success": pub_success,
            "failed": pub_failed,
            "success_rate": pub_success_rate
        },
        "piggyback": {
            "collected": piggy_collected,
            "published": piggy_published,
            "winning": piggy_winning
        },
        "team_metrics": {
            "active_operators": active_operators_count,
            "total_operators": all_operators_count,
            "total_actions": total_actions,
            "per_capita_actions": per_capita_output
        }
    }


@router.get("/leaderboard", summary="管理驾驶舱：员工人效排行榜与工作量矩阵")
def get_management_leaderboard(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    store_id: Optional[int] = Query(None, description="筛选店铺"),
    sort_by: Optional[str] = Query("total_actions", description="排序维度: total_actions, sourcing, publishing, piggyback"),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    start_dt, end_dt, _, _ = _parse_date_range(start_date, end_date)

    users = db.query(User).filter(User.is_active == True).order_by(User.id.asc()).all()

    # 查询所有店铺元数据
    stores_dict = {s.id: s.name for s in db.query(Store).all()}

    leaderboard = []

    for u in users:
        u_id = u.id

        # 授权店铺
        user_stores = db.query(UserStore).filter(UserStore.user_id == u_id).all()
        store_names = [stores_dict.get(us.store_id, f"店#{us.store_id}") for us in user_stores if us.store_id in stores_dict]

        # 1. 选品采集数
        sourcing_cnt = db.query(Product).filter(
            Product.user_id == u_id,
            Product.created_at.between(start_dt, end_dt)
        ).count()

        # 2. AI清洗数
        cleaning_cnt = db.query(TaskLog).filter(
            TaskLog.user_id == u_id,
            TaskLog.task_type.in_(["CLEAN", "BATCH_CLEAN"]),
            TaskLog.created_at.between(start_dt, end_dt)
        ).count()

        # 3. 侵权质检数
        compliance_cnt = db.query(TaskLog).filter(
            TaskLog.user_id == u_id,
            TaskLog.task_type.in_(["COMPLIANCE", "BATCH_COMPLIANCE", "PIGGYBACK_COMPLIANCE"]),
            TaskLog.created_at.between(start_dt, end_dt)
        ).count()

        # 4. 刊登发布数 (优先从 ProductStoreListing，次选 TaskLog)
        pub_psl_q = db.query(ProductStoreListing).filter(
            ProductStoreListing.user_id == u_id,
            ProductStoreListing.submitted_at.between(start_dt, end_dt)
        )
        if store_id:
            pub_psl_q = pub_psl_q.filter(ProductStoreListing.store_id == store_id)

        pub_total = pub_psl_q.count()
        pub_succ = pub_psl_q.filter(ProductStoreListing.status.in_(["SUBMITTED", "ACTIVE"])).count()
        pub_fail = pub_psl_q.filter(ProductStoreListing.status == "FAILED").count()

        if pub_total == 0:
            pub_log_q = db.query(TaskLog).filter(
                TaskLog.user_id == u_id,
                TaskLog.task_type.in_(["SUBMIT_LISTING", "BATCH_PUBLISH"]),
                TaskLog.created_at.between(start_dt, end_dt)
            )
            pub_total = pub_log_q.count()
            pub_succ = pub_log_q.filter(TaskLog.status == "SUCCESS").count()
            pub_fail = pub_log_q.filter(TaskLog.status == "FAILED").count()

        pub_rate = round((pub_succ / pub_total * 100), 1) if pub_total > 0 else 100.0

        # 5. Makro 跟品
        piggy_q = db.query(MakroPiggybackItem).filter(
            MakroPiggybackItem.user_id == u_id,
            MakroPiggybackItem.created_at.between(start_dt, end_dt)
        )
        if store_id:
            piggy_q = piggy_q.filter(MakroPiggybackItem.store_id == store_id)
        piggy_cnt = piggy_q.count()
        piggy_pub = piggy_q.filter(MakroPiggybackItem.status == "ACTIVE").count()

        # 6. 改价与底价调整次数
        reprice_cnt = db.query(TaskLog).filter(
            TaskLog.user_id == u_id,
            TaskLog.task_type.in_(["REPRICE_UPDATE", "BATCH_PRICE", "PIGGYBACK_BATCH_FLOOR"]),
            TaskLog.created_at.between(start_dt, end_dt)
        ).count()

        # 7. 全量操作总流水
        total_actions = db.query(TaskLog).filter(
            TaskLog.user_id == u_id,
            TaskLog.created_at.between(start_dt, end_dt)
        ).count()
        # 加上商品创建
        if total_actions == 0 and (sourcing_cnt > 0 or piggy_cnt > 0):
            total_actions = sourcing_cnt + piggy_cnt

        # 8. 首尾活跃工时区间
        first_log = db.query(TaskLog.created_at).filter(
            TaskLog.user_id == u_id,
            TaskLog.created_at.between(start_dt, end_dt)
        ).order_by(TaskLog.created_at.asc()).first()

        last_log = db.query(TaskLog.created_at).filter(
            TaskLog.user_id == u_id,
            TaskLog.created_at.between(start_dt, end_dt)
        ).order_by(TaskLog.created_at.desc()).first()

        first_time_str = first_log[0].strftime("%H:%M") if first_log else "--"
        last_time_str = last_log[0].strftime("%H:%M") if last_log else "--"
        work_span = f"{first_time_str} ~ {last_time_str}" if first_log else "暂无记录"

        leaderboard.append({
            "user_id": u_id,
            "username": u.username,
            "nickname": u.nickname or u.username,
            "role": u.role,
            "store_names": store_names,
            "sourcing_count": sourcing_cnt,
            "cleaning_count": cleaning_cnt,
            "compliance_count": compliance_cnt,
            "publish_total": pub_total,
            "publish_success": pub_succ,
            "publish_failed": pub_fail,
            "publish_success_rate": pub_rate,
            "piggyback_count": piggy_cnt,
            "piggyback_published": piggy_pub,
            "reprice_count": reprice_cnt,
            "total_actions": total_actions,
            "first_action_time": first_time_str,
            "last_action_time": last_time_str,
            "work_span": work_span
        })

    # 根据请求参数动态排序
    if sort_by == "sourcing":
        leaderboard.sort(key=lambda x: x["sourcing_count"], reverse=True)
    elif sort_by == "publishing":
        leaderboard.sort(key=lambda x: x["publish_total"], reverse=True)
    elif sort_by == "piggyback":
        leaderboard.sort(key=lambda x: x["piggyback_count"], reverse=True)
    else:
        # 默认综合总产出降序
        leaderboard.sort(key=lambda x: x["total_actions"], reverse=True)

    # 注入排名
    for idx, item in enumerate(leaderboard, start=1):
        item["rank"] = idx

    return {
        "leaderboard": leaderboard,
        "total_users": len(leaderboard)
    }


@router.get("/hourly-distribution", summary="管理驾驶舱：24小时作业时段节奏波形图")
def get_hourly_distribution(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    user_id: Optional[int] = Query(None, description="特定员工ID"),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    start_dt, end_dt, _, _ = _parse_date_range(start_date, end_date)

    query = db.query(
        func.strftime("%H", TaskLog.created_at).label("hour"),
        func.count(TaskLog.id).label("count")
    ).filter(TaskLog.created_at.between(start_dt, end_dt))

    if user_id:
        query = query.filter(TaskLog.user_id == user_id)

    raw_stats = query.group_by("hour").all()
    stats_map = {row[0]: row[1] for row in raw_stats if row[0] is not None}

    # 补齐 00 ~ 23 小时
    hours_labels = [f"{h:02d}" for h in range(24)]
    counts = [stats_map.get(h_str, 0) for h_str in hours_labels]

    total_actions = sum(counts)
    max_count = max(counts) if counts else 0
    peak_hour = hours_labels[counts.index(max_count)] if max_count > 0 else "--"

    return {
        "hours": hours_labels,
        "counts": counts,
        "total_actions": total_actions,
        "peak_hour": f"{peak_hour}:00" if peak_hour != "--" else "--",
        "max_hourly_count": max_count
    }


@router.get("/activity-logs", summary="管理驾驶舱：实时员工操作审计日志明细 (支持穿透下钻)")
def get_activity_logs(
    start_date: Optional[str] = Query(None, description="开始日期"),
    end_date: Optional[str] = Query(None, description="结束日期"),
    user_id: Optional[int] = Query(None, description="按员工筛选"),
    task_type: Optional[str] = Query(None, description="任务类型: COLLECT, CLEAN, SUBMIT_LISTING, PIGGYBACK_COLLECT 等"),
    status: Optional[str] = Query(None, description="状态: SUCCESS, FAILED, RUNNING"),
    keyword: Optional[str] = Query(None, description="搜索日志消息或商品ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=100),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    start_dt, end_dt, _, _ = _parse_date_range(start_date, end_date)

    query = db.query(TaskLog).filter(TaskLog.created_at.between(start_dt, end_dt))

    if user_id:
        query = query.filter(TaskLog.user_id == user_id)
    if task_type:
        query = query.filter(TaskLog.task_type == task_type)
    if status:
        query = query.filter(TaskLog.status == status)
    if keyword:
        kw = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                TaskLog.message.ilike(kw),
                TaskLog.operator_name.ilike(kw),
                TaskLog.request_id.ilike(kw)
            )
        )

    total = query.count()
    logs = query.order_by(TaskLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # 附加关联商品标题或额外信息
    items = []
    for l in logs:
        # 解析 detail_logs JSON
        parsed_details = None
        if l.detail_logs:
            try:
                parsed_details = json.loads(l.detail_logs)
            except Exception:
                parsed_details = l.detail_logs

        items.append({
            "id": l.id,
            "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else "",
            "finished_at": l.finished_at.strftime("%Y-%m-%d %H:%M:%S") if l.finished_at else "",
            "user_id": l.user_id,
            "operator_name": l.operator_name or "系统",
            "task_type": l.task_type,
            "status": l.status,
            "product_id": l.product_id,
            "request_id": l.request_id,
            "message": l.message or "",
            "detail_logs": parsed_details
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items
    }


@router.get("/export-workload", summary="导出考勤与员工工作量报表 (CSV 格式)")
def export_workload_csv(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    # 复用 leaderboard 逻辑提取全量矩阵
    leaderboard_res = get_management_leaderboard(
        start_date=start_date,
        end_date=end_date,
        store_id=None,
        sort_by="total_actions",
        admin=admin,
        db=db
    )
    data = leaderboard_res.get("leaderboard", [])

    output = io.StringIO()
    # 写入 UTF-8 BOM，确保 Windows Excel 打开不乱码
    output.write("\ufeff")
    writer = csv.writer(output)

    # 表头
    headers = [
        "排名", "员工账号", "员工姓名", "系统角色", "负责店铺",
        "选品采集数", "AI清洗数", "合规质检数", "刊登总数", "刊登成功数",
        "刊登失败数", "刊登成功率(%)", "Makro跟品数", "跟品上架数", "调价次数",
        "总操作产出量", "首笔操作时间", "末笔操作时间", "出勤工时跨度"
    ]
    writer.writerow(headers)

    for item in data:
        writer.writerow([
            item["rank"],
            item["username"],
            item["nickname"],
            "管理员" if item["role"] == "ADMIN" else "运营员工",
            " / ".join(item.get("store_names", [])) or "全店",
            item["sourcing_count"],
            item["cleaning_count"],
            item["compliance_count"],
            item["publish_total"],
            item["publish_success"],
            item["publish_failed"],
            f"{item['publish_success_rate']}%",
            item["piggyback_count"],
            item["piggyback_published"],
            item["reprice_count"],
            item["total_actions"],
            item["first_action_time"],
            item["last_action_time"],
            item["work_span"]
        ])

    s_name = start_date or datetime.now().strftime("%Y-%m-%d")
    e_name = end_date or s_name
    filename = f"operator_workload_{s_name}_to_{e_name}.csv"

    output.seek(0)
    return Response(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )


@router.get("/my-summary", summary="员工个人视角：我的今日战报 (普通员工与管理员均可访问)")
def get_my_daily_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    now = datetime.now()
    start_dt = datetime.combine(now.date(), time.min)
    end_dt = datetime.combine(now.date(), time.max)
    u_id = current_user.id

    # 今日个人数据
    today_sourcing = db.query(Product).filter(
        Product.user_id == u_id,
        Product.created_at.between(start_dt, end_dt)
    ).count()

    today_cleaning = db.query(TaskLog).filter(
        TaskLog.user_id == u_id,
        TaskLog.task_type.in_(["CLEAN", "BATCH_CLEAN"]),
        TaskLog.created_at.between(start_dt, end_dt)
    ).count()

    today_publishing = db.query(ProductStoreListing).filter(
        ProductStoreListing.user_id == u_id,
        ProductStoreListing.submitted_at.between(start_dt, end_dt)
    ).count()
    if today_publishing == 0:
        today_publishing = db.query(TaskLog).filter(
            TaskLog.user_id == u_id,
            TaskLog.task_type.in_(["SUBMIT_LISTING", "BATCH_PUBLISH"]),
            TaskLog.created_at.between(start_dt, end_dt)
        ).count()

    today_piggyback = db.query(MakroPiggybackItem).filter(
        MakroPiggybackItem.user_id == u_id,
        MakroPiggybackItem.created_at.between(start_dt, end_dt)
    ).count()

    today_actions = db.query(TaskLog).filter(
        TaskLog.user_id == u_id,
        TaskLog.created_at.between(start_dt, end_dt)
    ).count()
    if today_actions == 0:
        today_actions = today_sourcing + today_piggyback

    # 计算今日团队排名
    all_users = db.query(User).filter(User.is_active == True).all()
    scores = []
    for u in all_users:
        u_acts = db.query(TaskLog).filter(
            TaskLog.user_id == u.id,
            TaskLog.created_at.between(start_dt, end_dt)
        ).count()
        scores.append((u.id, u_acts))
    
    scores.sort(key=lambda x: x[1], reverse=True)
    my_rank = 1
    for r_idx, (uid, sc) in enumerate(scores, start=1):
        if uid == u_id:
            my_rank = r_idx
            break

    # 激励文案
    encouragement = "新的一天开始了，加油冲榜！"
    if today_actions >= 50:
        encouragement = "太棒了！今日产出遥遥领先，妥妥的团队销冠！🏆"
    elif today_actions >= 20:
        encouragement = "节奏非常棒，稳步推进，继续保持！✨"
    elif today_actions > 0:
        encouragement = "已完成初步作业，向更高的目标迈进吧！💪"

    return {
        "user_id": u_id,
        "username": current_user.username,
        "nickname": current_user.nickname or current_user.username,
        "today_sourcing": today_sourcing,
        "today_cleaning": today_cleaning,
        "today_publishing": today_publishing,
        "today_piggyback": today_piggyback,
        "today_actions": today_actions,
        "team_rank": my_rank,
        "total_operators": len(all_users),
        "encouragement": encouragement
    }
