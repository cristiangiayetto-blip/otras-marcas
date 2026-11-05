#!/usr/bin/env python3
"""
otras_marcas.py — Datos del panel Otras marcas (indice.json, meses/*.json).

Para cada mes arma la venta y la cobertura de las marcas que no son Pepsico ni
Georgalos, contra un objetivo que es lo vendido el mes anterior:

    meses/AAAA-MM.json               marcas (con submarcas), vendedores por
                                     marca, los dias habiles del mes y la
                                     facturacion total por empresa y por dia
    indice.json                      meses disponibles y hora de actualizacion
    fecha.txt                        fecha de actualizacion, para la tarjeta del portal

Criterios:

    venta      importe neto sin IVA, por FECHA DE ENTREGA del mes. VEN y DEB
               suman, todo lo demas (devoluciones) resta.
    objetivo   el promedio mensual de la venta de los 3 meses anteriores (o
               los que haya en la base desde PRIMER_DATO), con el mismo criterio.
    cobertura  clientes con compra neta positiva de la marca en el mes. El
               total cuenta cada cliente una sola vez. La cobertura objetivo es
               el promedio de clientes por mes de esos mismos meses.
    vendedores en cada marca aparecen todos los que vendieron alguna de estas
               marcas en el mes o en los de referencia, aunque sea en cero.
    ritmo      dias habiles (lunes a sabado, sin feriados) transcurridos sobre
               los del mes.
    facturacion todas las marcas, no solo estas: importe total con IVA, VEN y
               DEB menos devoluciones, por FECHA DE CARGA (fecha de pedido). Es el
               criterio del cuadro "Facturacion por empresa" del portal.

Las marcas salen del proveedor del articulo (catalogo de la base), salvo:
    - Pilaga (proveedor 142): las tostadas (codigoMarca 25) son PILAGA y el
      arroz Molinos Ala es ADECO AGRO.
    - LCB son dos proveedores: Tayal (517) e Hileret (10, Lagopuelo).
    - Softys se abre en submarcas por la descripcion.

Fuente: la base propia de Gescom (https://base.tienda-perfecta.workers.dev,
secreto BASE_CLAVE), /api/lineas-proveedor por fecha de entrega. No le pide
nada a Gescom.

Uso:
    python tools/otras_marcas.py                 mes en curso (y el anterior
                                                 los primeros 7 dias)
    python tools/otras_marcas.py 2026-09 2026-10 esos meses
"""
import calendar
import datetime as dt
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

DIR = Path(__file__).resolve().parent.parent   # raiz del repo: ahi lee la pagina
BASE = "https://base.tienda-perfecta.workers.dev"
TZ_AR = dt.timezone(dt.timedelta(hours=-3))
PRIMER_DATO = "2026-08"  # la base tiene datos desde el 1/8/2026
PRIMER_MES = "2026-09"   # primer mes con al menos un mes de referencia
MESES_OBJETIVO = 3       # meses que se promedian para el objetivo
# No son vendedores de calle: no van en las tablas por vendedor (su venta si cuenta en las marcas).
SIN_TABLA = {"20", "31"}   # Deposito, FLOR

# Orden en que se muestran: la lista de proveedores del 5/11/2026 (sin Pepsico ni Georgalos, que tienen su panel).
# Pilaga S.A. (142) trae dos marcas: Pilaga y Adeco Agro. Solo se muestran las que tienen stock (ver ACTIVAS).
MARCAS = ["SOFTYS", "ECOVITA", "LCB", "FECOVITA", "HARINAS CHACABUCO", "ENTRENUTS", "CASTELL", "EL CARMEN",
          "LAGOPUELO", "LA QUESERA", "L3N", "PILAGA", "ADECO AGRO", "PEHUENIAS", "MENOYO", "MORIXE",
          "LA DELICIA", "ROFINA", "FUTURE BEVERAGES"]
PROVEEDORES = {
    "142": None,              # PILAGA / ADECO AGRO, segun codigoMarca
    "517": "LCB", "10": "LAGOPUELO",    # 10 = Lagopuelo S.A. (Hileret), aparte de LCB (Tayal)
    "443": "SOFTYS", "351": "ECOVITA", "495": "FECOVITA", "359": "HARINAS CHACABUCO", "361": "ENTRENUTS",
    "547": "CASTELL", "594": "EL CARMEN", "94": "LA QUESERA", "141": "L3N", "589": "PEHUENIAS",
    "603": "MENOYO", "604": "MORIXE", "25": "LA DELICIA", "391": "ROFINA", "588": "FUTURE BEVERAGES",
}
# Las de MARCAS que hoy tienen stock: se arma en main con la foto de stock de la base.
ACTIVAS = set(MARCAS)
STOCK = {}
# Objetivo fijo de venta por vendedor (neto sin IVA, por mes) en lugar del promedio: pedido del 5/11/2026.
OBJETIVO_FIJO = {
    "FECOVITA": 25000, "L3N": 450000,
    "PILAGA": {"11": 300000, "7": 250000, "2": 200000, "10": 200000},   # Melanie Larrama, Ramiro Rando, Julian Nuñez y Matias Parada; el resto, el promedio
}
STOCK_MINIMO = 10   # un articulo cuenta como "con stock" si tiene mas de 10 unidades (sumando depositos)
SUMAN = {"VEN", "DEB"}
EMPRESAS = [("1", "Tienda Perfecta"), ("2", "Rambla de los Lobos"), ("99", "X")]


def cod(x):
    return "" if x is None else str(x).strip()


def base_get(ruta, **params):
    for intento in range(3):
        try:
            r = requests.get(BASE + ruta, params=params, timeout=240,
                             headers={"x-clave": os.environ["BASE_CLAVE"].strip()})
            if r.status_code < 500:
                break
        except requests.ConnectionError:
            if intento == 2:
                raise
        time.sleep(10 * (intento + 1))
    r.raise_for_status()
    return r.json()


def marca_de(art):
    """Marca del articulo, solo si es una de las que se muestran (con stock)."""
    m = marca_base(art)
    return m if m in ACTIVAS else None


def marca_base(art):
    prov = cod(art.get("codigoProveedor"))
    if prov == "142":
        return "PILAGA" if cod(art.get("codigoMarca")) == "25" else "ADECO AGRO"
    return PROVEEDORES.get(prov)


def submarca_de(art):
    prov = cod(art.get("codigoProveedor"))
    if prov != "443":
        return None
    d = (art.get("descripcion") or "").upper()
    for clave, nombre in (("BABYSEC", "Babysec"), ("HIGI", "Higienol"), ("COTIDIAN", "Cotidian"),
                          ("ELITE", "Elite"), ("SUSSEX", "Sussex")):
        if clave in d:
            return nombre
    if d[:3] in ("TF ", "PD ") or "LADYSOFT" in d:
        return "Ladysoft"
    return "Otros Softys"


def articulos():
    """(codigo, empresa) -> articulo de los proveedores del panel. La empresa 99 usa los de la 1."""
    arts = {}
    for a in base_get("/api/catalogo/articulos"):
        if cod(a.get("codigoProveedor")) in PROVEEDORES:
            arts[(cod(a.get("codigo")), cod(a.get("codigoEmpresa")))] = a
    return arts


def mes_anterior(ym):
    y, m = map(int, ym.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def habiles(ym, hoy):
    y, m = map(int, ym.split("-"))
    dias = [dt.date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]
    lab = [d for d in dias if d.weekday() != 6]
    return len(lab), sum(1 for d in lab if d <= hoy)


def fila(objetivo, venta, cob_obj, cob_act):
    objetivo, venta = round(objetivo), round(venta)
    return {
        "objetivo": objetivo, "venta": venta,
        "cumpl": round(venta / objetivo, 4) if objetivo > 0 else None,
        "falta": max(objetivo - venta, 0),
        "cob_obj": cob_obj, "cob_act": cob_act,
        "cumpl_cob": round(cob_act / cob_obj, 4) if cob_obj > 0 else None,
        "faltan_cli": max(cob_obj - cob_act, 0),
    }


def meses_ref(ym):
    """Los MESES_OBJETIVO meses anteriores a ym que ya estan en la base, del mas viejo al mas nuevo."""
    ref, m = [], ym
    for _ in range(MESES_OBJETIVO):
        m = mes_anterior(m)
        if m >= PRIMER_DATO:
            ref.insert(0, m)
    return ref


def resumir(neto_por_cli, n_ref):
    """{(per, cli): neto} -> objetivo y cobertura objetivo promedio de los n_ref meses, venta y cobertura actual.
    per es "act" o el mes de referencia (AAAA-MM)."""
    obj = sum(n for (p, _), n in neto_por_cli.items() if p != "act") / n_ref
    act = sum(n for (p, _), n in neto_por_cli.items() if p == "act")
    cob_obj = round(sum(1 for (p, _), n in neto_por_cli.items() if p != "act" and n > 0) / n_ref)
    cob_act = sum(1 for (p, _), n in neto_por_cli.items() if p == "act" and n > 0)
    return fila(obj, act, cob_obj, cob_act)


def facturacion(ym, vendedores):
    """Facturacion total (todas las marcas) del mes ym por dia de carga, vendedor y empresa.
    filas = [fecha, vendedor, empresa, monto]: la pagina arma con eso el dia, la semana o el mes."""
    y, m = map(int, ym.split("-"))
    ult = calendar.monthrange(y, m)[1]
    acc = defaultdict(float)
    por_cli = defaultdict(float)   # (fecha, vendedor, cliente) -> total, para la cobertura
    # de a una semana: /api/ventas devuelve el JSON entero de cada comprobante
    for d1 in range(1, ult + 1, 7):
        desde, hasta = f"{ym}-{d1:02d}", f"{ym}-{min(d1 + 6, ult):02d}"
        for v in base_get("/api/ventas", desde=desde, hasta=hasta, por="pedido"):
            tipo = cod(v.get("codigoTipoVenta"))
            signo = 1 if tipo in SUMAN else -1 if tipo.startswith("DEV") else 0
            fecha = (v.get("fechaPedido") or "")[:10]
            if signo and fecha[:7] == ym:
                monto = signo * float(v.get("importeTotal") or 0)
                acc[(fecha, cod(v.get("codigoVendedor")), cod(v.get("codigoEmpresa")))] += monto
                por_cli[(fecha, cod(v.get("codigoVendedor")), cod(v.get("codigoCliente")))] += monto
    filas = [[f, ven, e, round(n)] for (f, ven, e), n in sorted(acc.items()) if round(n)]
    # clientes con compra neta de cualquier cosa, por dia de carga y vendedor
    clientes = defaultdict(dict)
    for (f, ven, cli), n in sorted(por_cli.items()):
        if n > 0:
            clientes[f].setdefault(ven, []).append(cli)
    total = sum(f[3] for f in filas)
    empresas = [{"emp": e, "nombre": n, "total": sum(f[3] for f in filas if f[2] == e)} for e, n in EMPRESAS]
    for e in empresas:
        e["pct"] = round(e["total"] / total, 4) if total else None
    nombres = {ven: vendedores.get(ven) or f"Vendedor {ven}" for ven in sorted({f[1] for f in filas})}
    return {"total": total, "empresas": empresas, "filas": filas, "nombres": nombres, "clientes": clientes}


def cobertura_dia(ym, arts):
    """Clientes con compra neta de cada marca por dia de carga y vendedor: {fecha: {vendedor: {marca: [clientes]}}}.
    Con facturacion.clientes (los que compraron cualquier cosa) la pagina arma la cobertura del dia, la semana o el mes."""
    y, m = map(int, ym.split("-"))
    desde, hasta = f"{ym}-01", f"{ym}-{calendar.monthrange(y, m)[1]:02d}"
    acc = defaultdict(float)
    arts_dia = defaultdict(float)   # (fecha, vendedor, marca, articulo) -> neto
    for prov in PROVEEDORES:
        for l in base_get("/api/lineas-proveedor", proveedor=prov, desde=desde, hasta=hasta, por="pedido"):
            emp = cod(l.get("emp"))
            art = arts.get((cod(l.get("item")), "1" if emp == "99" else emp))
            fecha = (l.get("fp") or "")[:10]
            if not art or cod(art.get("codigoProveedor")) != prov or fecha[:7] != ym or not marca_de(art):
                continue
            neto = float(l.get("neto") or 0) * (1 if l.get("tipo") in SUMAN else -1)
            acc[(fecha, cod(l.get("ven")), marca_de(art), cod(l.get("cli")))] += neto
            arts_dia[(fecha, cod(l.get("ven")), marca_de(art), cod(art.get("codigo")))] += neto
    out = defaultdict(lambda: defaultdict(dict))
    for (f, ven, marca, cli), n in sorted(acc.items()):
        if n > 0:
            out[f][ven].setdefault(marca, []).append(cli)
    # articulos distintos vendidos por dia de carga, vendedor y marca, para comparar con los que hay en stock
    vendidos = defaultdict(lambda: defaultdict(dict))
    for (f, ven, marca, item), n in sorted(arts_dia.items()):
        if n > 0:
            vendidos[f][ven].setdefault(marca, []).append(item)
    return out, vendidos


def stock_por_marca(arts):
    """Articulos con mas de STOCK_MINIMO unidades (sumando depositos) de cada marca: {marca: [codigos]}.
    Es la foto que guardo el robot de la base; el stock no viene por empresa: se busca el articulo en la 1 y en la 2."""
    out = defaultdict(set)
    for s in base_get("/gescom/data/cmd/inventario/api/v1/get-stock"):
        item = cod(s.get("codigoItem"))
        if sum(float(x.get("cantidad") or 0) for x in s.get("stock") or []) <= STOCK_MINIMO:
            continue
        art = arts.get((item, "1")) or arts.get((item, "2"))
        if art and marca_base(art):
            out[marca_base(art)].add(item)
    return {m: sorted(v) for m, v in sorted(out.items())}


def fijar_objetivos(marcas, vend):
    """Marcas con objetivo fijo por vendedor (OBJETIVO_FIJO) en vez del promedio de los meses anteriores.
    El objetivo de la marca pasa a ser el fijo por la cantidad de vendedores de la tabla, y los totales se corrigen
    con la diferencia. La cobertura objetivo no cambia."""
    def rehacer(r, objetivo):
        r.update(fila(objetivo, r["venta"], r["cob_obj"], r["cob_act"]))
    for marca, fijo in OBJETIVO_FIJO.items():
        # un monto para todos los vendedores, o {vendedor: monto} solo para algunos (los demas siguen con el promedio)
        por_ven = fijo if isinstance(fijo, dict) else None
        filas = [r for r in vend if r["marca"] == marca and (por_ven is None or r["ven"] in por_ven)]
        m = next((r for r in marcas if r["marca"] == marca and r["sub"] == "Total"), None)
        if not filas or not m:
            continue
        dif = 0
        for r in filas:
            obj = por_ven[r["ven"]] if por_ven else fijo
            tot = next(t for t in vend if t["ven"] == r["ven"] and t["marca"] == "TOTAL")
            rehacer(tot, tot["objetivo"] - r["objetivo"] + obj)
            dif += obj - r["objetivo"]
            rehacer(r, obj)
        t = next(r for r in marcas if r["marca"] == "TOTAL")
        nuevo = m["objetivo"] + dif if por_ven else fijo * len(filas)
        rehacer(t, t["objetivo"] - m["objetivo"] + nuevo)
        rehacer(m, nuevo)


def armar_mes(ym, hoy, arts, vendedores, datos_al):
    ref = meses_ref(ym)
    n_ref = len(ref)

    # neto por (marca, submarca, vendedor, periodo, cliente). El endpoint acepta hasta 62 dias: se pide mes por mes.
    acc = defaultdict(float)
    for mm in ref + [ym]:
        y, m = map(int, mm.split("-"))
        desde, hasta = f"{mm}-01", f"{mm}-{calendar.monthrange(y, m)[1]:02d}"
        for prov in PROVEEDORES:
            for l in base_get("/api/lineas-proveedor", proveedor=prov, desde=desde, hasta=hasta, por="entrega"):
                emp = cod(l.get("emp"))
                art = arts.get((cod(l.get("item")), "1" if emp == "99" else emp))
                # el endpoint junta articulos por codigo sin empresa: se confirma el proveedor aca
                if not art or cod(art.get("codigoProveedor")) != prov or not marca_de(art):
                    continue
                fe = (l.get("fe") or "")[:7]
                if fe not in ref and fe != ym:
                    continue
                per = "act" if fe == ym else fe
                neto = float(l.get("neto") or 0) * (1 if l.get("tipo") in SUMAN else -1)
                acc[(marca_de(art), submarca_de(art), cod(l.get("ven")), per, cod(l.get("cli")))] += neto

    def juntar(clave):
        g = defaultdict(lambda: defaultdict(float))
        for (marca, sub, ven, per, cli), n in acc.items():
            k = clave(marca, sub, ven)
            if k is not None:
                g[k][(per, cli)] += n
        return g

    por_marca = juntar(lambda marca, sub, ven: marca)
    por_sub = juntar(lambda marca, sub, ven: (marca, sub) if sub else None)
    total = juntar(lambda marca, sub, ven: "TOTAL")
    por_ven = juntar(lambda marca, sub, ven: (ven, marca))
    ven_total = juntar(lambda marca, sub, ven: (ven, "TOTAL"))

    vacia = fila(0, 0, 0, 0)
    marcas = []
    for i, marca in enumerate([m for m in MARCAS if m in ACTIVAS], 1):
        marcas.append({"marca": marca, "sub": "Total", "orden": i,
                       **(resumir(por_marca[marca], n_ref) if marca in por_marca else vacia)})
        subs = [{"marca": marca, "sub": s, "orden": i, **resumir(v, n_ref)} for (mm, s), v in por_sub.items() if mm == marca]
        marcas += sorted(subs, key=lambda r: -r["objetivo"])
    marcas.append({"marca": "TOTAL", "sub": "Total", "orden": 99, **(resumir(total["TOTAL"], n_ref) if total else vacia)})

    # todos los vendedores en todas las marcas, aunque no la vendan
    vend = []
    for ven in sorted({ven for (ven, _) in ven_total} - SIN_TABLA, key=lambda v: (len(v), v)):
        for marca in [m for m in MARCAS if m in ACTIVAS] + ["TOTAL"]:
            v = (ven_total if marca == "TOTAL" else por_ven).get((ven, marca))
            vend.append({"ven": ven, "vendedor": vendedores.get(ven) or f"Vendedor {ven}", "marca": marca,
                         **(resumir(v, n_ref) if v else vacia)})
    fijar_objetivos(marcas, vend)
    vend.sort(key=lambda r: (r["marca"], -r["objetivo"]))

    # cartera de cada marca por vendedor: clientes con compra neta en alguno de los meses de referencia
    cartera = defaultdict(dict)
    for (ven, marca), g in sorted(por_ven.items()):
        clis = sorted({cli for (p, cli), n in g.items() if p != "act" and n > 0}, key=lambda c: (len(c), c))
        if clis and ven not in SIN_TABLA:
            cartera[ven][marca] = clis

    hab_mes, hab_hoy = habiles(ym, hoy)
    cob_dia, arts_dia = cobertura_dia(ym, arts)
    return {
        "mes": ym, "mes_ant": ref[-1], "meses_ref": ref, "hoy": hoy.isoformat(),
        "habiles_mes": hab_mes, "habiles_hasta_hoy": hab_hoy, "datos_al": datos_al,
        "marcas": marcas, "vendedores": vend, "facturacion": facturacion(ym, vendedores),
        "cobertura_dia": cob_dia, "articulos_dia": arts_dia, "cartera": cartera,
        "stock": STOCK, "marcas_activas": [m for m in MARCAS if m in ACTIVAS],
    }


def main(args):
    if not os.environ.get("BASE_CLAVE", "").strip():
        print("Falta el secreto BASE_CLAVE: no se puede leer la base; se dejan los JSON anteriores sin tocar.")
        return
    hoy = dt.datetime.now(TZ_AR).date()
    actual = hoy.strftime("%Y-%m")
    meses = args or ([mes_anterior(actual)] if hoy.day <= 7 else []) + [actual]
    meses = [m for m in meses if PRIMER_MES <= m <= actual]

    arts = articulos()
    global STOCK
    STOCK = stock_por_marca(arts)
    ACTIVAS.clear()
    ACTIVAS.update(m for m in MARCAS if STOCK.get(m))
    print("Sin stock, no se muestran:", [m for m in MARCAS if m not in ACTIVAS] or "ninguna")
    vendedores = {cod(v.get("codigo")): (v.get("nombre") or "").strip() for v in base_get("/api/catalogo/vendedores")}
    corridas = base_get("/api/estado").get("corridas") or []
    datos_al = max((c.get("inicio") or "" for c in corridas if c.get("estado") == "ok"), default=None)

    (DIR / "meses").mkdir(parents=True, exist_ok=True)
    for ym in meses:
        datos = armar_mes(ym, hoy, arts, vendedores, datos_al)
        (DIR / "meses" / f"{ym}.json").write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        t = next(r for r in datos["marcas"] if r["marca"] == "TOTAL")
        print(f"{ym}: objetivo {t['objetivo']:,} venta {t['venta']:,} cobertura {t['cob_act']}/{t['cob_obj']}")

    disponibles = sorted((p.stem for p in (DIR / "meses").glob("*.json")), reverse=True)
    indice = {"meses": disponibles, "actualizado": dt.datetime.now(TZ_AR).isoformat(timespec="minutes")}
    (DIR / "indice.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
    (DIR / "fecha.txt").write_text(f"Ultima actualizacion: {hoy.isoformat()}\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
