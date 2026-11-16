"""One model call per shop: business domain and column roles, used only as a fallback.

Alias matching stays first. The model's assignment fills a role only when no alias
matched, and domain-specific roles are used only when the model also classifies the
shop as that domain. Every assignment is validated against the real catalog.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .io import dumps

ROLE_PROMPT_VERSION = "roles-v3"

# role -> (internal field name, description shown to the model)
CORE_ROLES = {
    "order_id": ("order_id", "mã đơn / hoá đơn / phiếu bán"),
    "order_date": ("date", "ngày bán / ngày đặt / ngày lập đơn"),
    "order_time": ("time", "giờ tạo đơn"),
    "product_id": ("product_id", "mã hàng"),
    "product_name": ("product_name", "tên hàng / món / sản phẩm"),
    "product_group": ("group", "nhóm hàng / loại hàng / danh mục"),
    "quantity": ("quantity", "số lượng bán trên dòng hàng (theo đơn vị của dòng)"),
    "base_quantity": ("base_quantity", "số lượng đã quy đổi ra đơn vị cơ bản"),
    "unit": ("unit", "đơn vị tính của dòng"),
    "line_amount": ("revenue", "thành tiền của DÒNG hàng (sau giảm dòng)"),
    "unit_price": ("unit_price", "đơn giá bán"),
    "order_amount": ("order_total", "tổng tiền cả đơn / khách phải trả của đơn (thường ở bảng hoá đơn)"),
    "order_goods_amount": ("order_revenue", "tổng tiền hàng của đơn trước giảm giá cả đơn"),
    "customer_id": ("customer_id", "mã khách"),
    "customer_name": ("customer_name", "tên khách"),
    "status": ("status", "trạng thái đơn: cột có giá trị như Hoàn thành/Huỷ/Trả hàng/Đang giao; RẤT quan trọng, gán ở mọi bảng có cột này"),
    "channel": ("channel", "kênh bán (tại quầy/online/sàn)"),
    "base_unit": ("base_unit", "đơn vị cơ bản của mặt hàng (danh mục hoặc bảng quy đổi)"),
    "large_unit": ("large_unit", "đơn vị lớn trong bảng quy đổi"),
    "conversion_factor": ("factor", "hệ số quy đổi 1 đơn vị lớn ra đơn vị cơ bản"),
    "payment_method": ("payment_method", "hình thức thanh toán"),
    "employee": ("employee", "nhân viên bán / thu ngân"),
}
DOMAIN_ROLES = {
    "vehicle_repair": {
        "plate": ("plate", "biển số xe"), "job": ("job", "việc đã làm / nội dung sửa"), "mechanic": ("mechanic", "thợ làm"),
        "vehicle_model": ("model", "dòng xe / loại xe"), "visit_date": ("ticket_date", "NGÀY xe vào / ngày sửa (cột ngày)"),
        "ticket_amount": ("total", "tổng tiền thu của lượt sửa"), "labor_amount": ("labor", "tiền công"), "parts_amount": ("parts", "tiền phụ tùng / vật tư"),
        "ticket_kind": ("kind", "loại phiếu (sửa / bảo hành)"), "ticket_note": ("notes", "ghi chú phiếu"),
    },
    "mother_baby": {
        "size": ("size", "size bỉm / quần áo trong danh mục"), "milk_stage": ("stage", "số sữa (giai đoạn) trong danh mục"), "brand": ("brand", "hãng / thương hiệu"),
        "registered_date": ("registered", "NGÀY đăng ký thành viên của khách (cột ngày)"),
        "voucher_amount": ("voucher", "tiền giảm voucher của hoá đơn"), "points_amount": ("points", "tiền giảm nhờ dùng điểm"), "ship_fee": ("ship", "phí ship / giao hàng"),
    },
    "phone_store": {
        "tradein_amount": ("tradein", "tiền thu máy cũ của khách (trừ vào đơn)"), "payable_amount": ("payable", "khách phải trả sau trừ máy cũ"),
        "imei": ("imei", "IMEI / serial máy"), "warranty_received_date": ("receive_date", "NGÀY nhận máy bảo hành (cột ngày)"),
        "installment_due_date": ("due_date", "NGÀY hạn góp của kỳ trả góp (cột ngày, không phải số kỳ)"), "installment_amount": ("due_amount", "số tiền phải góp mỗi kỳ"),
        "installment_paid_amount": ("paid_amount", "số tiền đã góp"), "installment_paid_date": ("paid_date", "NGÀY khách đã góp (cột ngày)"),
        "ship_fee": ("ship", "phí ship / giao hàng"),
    },
}
DOMAINS = ("grocery", "cafe", "restaurant", "pharmacy", "building_materials", "mother_baby", "phone_store", "vehicle_repair", "other")
ALL_ROLES = tuple(dict.fromkeys(list(CORE_ROLES) + [role for roles in DOMAIN_ROLES.values() for role in roles]))


class ColumnRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(max_length=120)
    column: str = Field(max_length=120)
    role: Literal[ALL_ROLES]  # type: ignore[valid-type]


class ShopRoles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: Literal[DOMAINS]  # type: ignore[valid-type]
    assignments: list[ColumnRole] = Field(max_length=120)


SYSTEM = ("Bạn đọc mô tả và danh sách bảng/cột của MỘT cửa hàng Việt Nam rồi trả JSON: domain là ngành của cửa hàng, assignments gán vai trò cho cột. "
          "Chỉ dùng đúng tên bảng và tên cột ASCII đã cho, không bịa. Mỗi vai trò tối đa một cột trong một bảng. Bỏ qua cột không khớp vai trò nào. "
          "Không gán tổng tiền cả đơn làm thành tiền dòng. Vai trò ngành chỉ gán khi cửa hàng thuộc ngành đó. Ngành: grocery=tạp hoá, cafe=cà phê/trà sữa, "
          "restaurant=quán ăn, pharmacy=nhà thuốc, building_materials=vật liệu xây dựng, mother_baby=mẹ và bé, phone_store=điện thoại, vehicle_repair=sửa xe.")


def render(catalog: dict, limit: int = 14000) -> str:
    lines = ["README:", catalog.get("readme", "")[:2500], "", "Vai trò:"]
    lines += [f"- {role}: {desc}" for role, (_, desc) in CORE_ROLES.items()]
    for domain, roles in DOMAIN_ROLES.items():
        lines += [f"- {role} (chỉ {domain}): {desc}" for role, (_, desc) in roles.items()]
    lines.append("\nBảng:")
    for table in catalog["tables"]:
        lines.append(f"TABLE {table['name']} ({table['rows']} dòng)")
        for column in table["columns"]:
            if not column.get("nonempty"):
                continue
            samples = list(column.get("values") or {})[:4] or column.get("samples", [])[:2]
            lines.append(f"  {column['name']} [{column['kind']}] gốc='{column['original']}' mẫu={dumps(samples)[:120]}")
    return "\n".join(lines)[:limit]


def validated(roles: ShopRoles | None, catalog: dict) -> dict:
    """{'domain': str, 'tables': {table: {internal_field: column}}} with only real columns."""
    if roles is None:
        return {"domain": "other", "tables": {}}
    columns = {table["name"]: {c["name"] for c in table["columns"] if c.get("nonempty")} for table in catalog["tables"]}
    fields = {role: field for role, (field, _) in CORE_ROLES.items()}
    domain_fields = {role: field for role, (field, _) in DOMAIN_ROLES.get(roles.domain, {}).items()}
    tables = {}
    for item in roles.assignments:
        field = fields.get(item.role) or domain_fields.get(item.role)
        if field and item.column in columns.get(item.table, ()):
            tables.setdefault(item.table, {}).setdefault(field, item.column)
    return {"domain": roles.domain, "tables": tables}


def cache_path(directory: Path, model: str) -> Path:
    key = hashlib.sha256(f"{ROLE_PROMPT_VERSION}|{model}".encode()).hexdigest()[:16]
    return Path(directory) / f"llm_roles-{key}.json"


async def infer_roles(provider, catalog: dict, directory: Path):
    """Cached per prepared shop, model and prompt version; returns (validated roles, call trace or None)."""
    path = cache_path(directory, provider.settings.model)
    if path.exists():
        return json.loads(path.read_text()), None
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": render(catalog)}]
    parsed, call = await provider.generate(messages, ShopRoles)
    result = validated(parsed, catalog)
    if parsed is not None:
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result, call


def fallback(catalog: dict, table: str, field: str, domain: str | None = None):
    """Model-assigned column for (table, field); domain fields only when the shop is that domain."""
    roles = catalog.get("llm_roles") or {}
    if domain is not None and roles.get("domain") != domain:
        return None
    return (roles.get("tables") or {}).get(table, {}).get(field)
