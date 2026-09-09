//! Bounds-checked BSII reader. It decodes the schema and walks every object,
//! retaining only a compact summary for the cross-language boundary.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BsiiHeader {
    pub version: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BsiiSummary {
    pub version: u32,
    pub definitions: u32,
    pub objects: u32,
}

pub fn inspect_header(bytes: &[u8]) -> Result<BsiiHeader, &'static str> {
    if bytes.len() < 8 || &bytes[..4] != b"BSII" {
        return Err("invalid_bsii_header");
    }
    let version = u32::from_le_bytes(bytes[4..8].try_into().unwrap());
    if !(1..=3).contains(&version) {
        return Err("unsupported_bsii_version");
    }
    Ok(BsiiHeader { version })
}

struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
    version: u32,
}
impl<'a> Reader<'a> {
    fn new(data: &'a [u8], version: u32) -> Self {
        Self {
            data,
            pos: 8,
            version,
        }
    }
    fn need(&self, n: usize) -> Result<(), String> {
        if self
            .pos
            .checked_add(n)
            .is_none_or(|end| end > self.data.len())
        {
            Err(format!("truncated_bsii_at_{}", self.pos))
        } else {
            Ok(())
        }
    }
    fn u8(&mut self) -> Result<u8, String> {
        self.need(1)?;
        let v = self.data[self.pos];
        self.pos += 1;
        Ok(v)
    }
    fn u16(&mut self) -> Result<u16, String> {
        self.need(2)?;
        let v = u16::from_le_bytes(self.data[self.pos..self.pos + 2].try_into().unwrap());
        self.pos += 2;
        Ok(v)
    }
    fn u32(&mut self) -> Result<u32, String> {
        self.need(4)?;
        let v = u32::from_le_bytes(self.data[self.pos..self.pos + 4].try_into().unwrap());
        self.pos += 4;
        Ok(v)
    }
    fn u64(&mut self) -> Result<u64, String> {
        self.need(8)?;
        let v = u64::from_le_bytes(self.data[self.pos..self.pos + 8].try_into().unwrap());
        self.pos += 8;
        Ok(v)
    }
    fn i16(&mut self) -> Result<i16, String> {
        Ok(self.u16()? as i16)
    }
    fn i32(&mut self) -> Result<i32, String> {
        Ok(self.u32()? as i32)
    }
    fn i64(&mut self) -> Result<i64, String> {
        Ok(self.u64()? as i64)
    }
    fn f32(&mut self) -> Result<f32, String> {
        Ok(f32::from_bits(self.u32()?))
    }
    fn string(&mut self) -> Result<String, String> {
        let n = self.u32()? as usize;
        self.need(n)?;
        let value = std::str::from_utf8(&self.data[self.pos..self.pos + n])
            .map_err(|_| "invalid_bsii_utf8".to_string())?
            .to_string();
        self.pos += n;
        Ok(value)
    }
}

fn encoded_string(reader: &mut Reader<'_>) -> Result<(), String> {
    reader.u64().map(|_| ())
}
fn encoded_id(reader: &mut Reader<'_>) -> Result<(), String> {
    let parts = reader.u8()?;
    if parts == 0xFF {
        reader.u64()?;
    } else {
        for _ in 0..parts {
            encoded_string(reader)?;
        }
    }
    Ok(())
}
fn array<T>(
    reader: &mut Reader<'_>,
    mut f: impl FnMut(&mut Reader<'_>) -> Result<T, String>,
) -> Result<(), String> {
    let count = reader.u32()? as usize;
    for _ in 0..count {
        f(reader)?;
    }
    Ok(())
}
fn vecn<T>(
    reader: &mut Reader<'_>,
    count: usize,
    mut f: impl FnMut(&mut Reader<'_>) -> Result<T, String>,
) -> Result<(), String> {
    for _ in 0..count {
        f(reader)?;
    }
    Ok(())
}
fn skip_value(reader: &mut Reader<'_>, ty: u32, _ordinal_count: u32) -> Result<(), String> {
    match ty {
        0x01 => {
            reader.string()?;
        }
        0x02 => {
            array(reader, |r| r.string())?;
        }
        0x03 => {
            encoded_string(reader)?;
        }
        0x04 => {
            array(reader, encoded_string)?;
        }
        0x05 => {
            reader.f32()?;
        }
        0x06 => {
            array(reader, |r| r.f32())?;
        }
        0x07 => {
            vecn(reader, 2, |r| r.f32())?;
        }
        0x08 => {
            array(reader, |r| vecn(r, 2, |rr| rr.f32()))?;
        }
        0x09 => {
            vecn(reader, 3, |r| r.f32())?;
        }
        0x0A => {
            array(reader, |r| vecn(r, 3, |rr| rr.f32()))?;
        }
        0x11 => {
            vecn(reader, 3, |r| r.i32())?;
        }
        0x12 => {
            array(reader, |r| vecn(r, 3, |rr| rr.i32()))?;
        }
        0x17 => {
            vecn(reader, 4, |r| r.f32())?;
        }
        0x18 => {
            array(reader, |r| vecn(r, 4, |rr| rr.f32()))?;
        }
        0x19 => {
            vecn(reader, if reader.version >= 2 { 8 } else { 7 }, |r| r.f32())?;
        }
        0x1A => {
            let n = if reader.version >= 2 { 8 } else { 7 };
            array(reader, |r| vecn(r, n, |rr| rr.f32()))?;
        }
        0x25 => {
            reader.i32()?;
        }
        0x26 => {
            array(reader, |r| r.i32())?;
        }
        0x27 | 0x2F => {
            reader.u32()?;
        }
        0x28 => {
            array(reader, |r| r.u32())?;
        }
        0x29 => {
            reader.i16()?;
        }
        0x2A => {
            array(reader, |r| r.i16())?;
        }
        0x2B => {
            reader.u16()?;
        }
        0x2C => {
            array(reader, |r| r.u16())?;
        }
        0x31 => {
            reader.i64()?;
        }
        0x32 => {
            array(reader, |r| r.i64())?;
        }
        0x33 => {
            reader.u64()?;
        }
        0x34 => {
            array(reader, |r| r.u64())?;
        }
        0x35 => {
            reader.u8()?;
        }
        0x36 => {
            array(reader, |r| r.u8())?;
        }
        0x37 => {
            reader.u32()?;
        }
        0x39 | 0x3B | 0x3D => {
            encoded_id(reader)?;
        }
        0x3A | 0x3C | 0x3E => {
            array(reader, encoded_id)?;
        }
        _ => return Err(format!("unsupported_bsii_type_{ty:02x}")),
    }
    Ok(())
}

pub fn parse_summary(bytes: &[u8]) -> Result<BsiiSummary, String> {
    let header = inspect_header(bytes).map_err(str::to_string)?;
    let mut reader = Reader::new(bytes, header.version);
    let mut definitions: Vec<Vec<(u32, u32)>> = Vec::new();
    let mut definition_count = 0;
    let mut objects = 0;
    while reader.pos < reader.data.len() {
        let block = reader.u32()?;
        if block == 0 {
            let valid = reader.u8()? != 0;
            let id = reader.u32()?;
            let _name = reader.string()?;
            let mut fields = Vec::new();
            loop {
                let ty = reader.u32()?;
                if ty == 0 {
                    break;
                }
                let _field_name = reader.string()?;
                let mut ordinal_count = 0;
                if ty == 0x37 {
                    ordinal_count = reader.u32()?;
                    for _ in 0..ordinal_count {
                        reader.u32()?;
                        reader.string()?;
                    }
                }
                fields.push((ty, ordinal_count));
            }
            if id as usize >= definitions.len() {
                definitions.resize(id as usize + 1, Vec::new());
            }
            definitions[id as usize] = if valid { fields } else { Vec::new() };
            if valid {
                definition_count += 1;
            }
            continue;
        }
        let fields = definitions
            .get(block as usize)
            .ok_or_else(|| "unknown_bsii_structure".to_string())?;
        encoded_id(&mut reader)?;
        for (ty, ordinal_count) in fields {
            skip_value(&mut reader, *ty, *ordinal_count)?;
        }
        objects += 1;
    }
    Ok(BsiiSummary {
        version: header.version,
        definitions: definition_count,
        objects,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reads_little_endian_version() {
        assert_eq!(inspect_header(b"BSII\x03\0\0\0").unwrap().version, 3);
    }
    #[test]
    fn rejects_unknown_version() {
        assert_eq!(
            inspect_header(b"BSII\x63\0\0\0"),
            Err("unsupported_bsii_version")
        );
    }
}
