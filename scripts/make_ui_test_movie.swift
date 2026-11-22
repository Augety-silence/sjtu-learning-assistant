// Five-second synthetic video for native playback-buffer acceptance only.
import Foundation
import AVFoundation
import CoreVideo

let destination = URL(fileURLWithPath: CommandLine.arguments[1])
let writer = try AVAssetWriter(outputURL: destination, fileType: .mp4)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: 320, AVVideoHeightKey: 180,
])
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input,
    sourcePixelBufferAttributes: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB,
        kCVPixelBufferWidthKey as String: 320, kCVPixelBufferHeightKey as String: 180])
writer.add(input)
guard writer.startWriting() else { throw writer.error! }
writer.startSession(atSourceTime: .zero)
for frame in 0..<150 {
    while !input.isReadyForMoreMediaData { Thread.sleep(forTimeInterval: 0.002) }
    var buffer: CVPixelBuffer?
    CVPixelBufferCreate(nil, 320, 180, kCVPixelFormatType_32ARGB, nil, &buffer)
    let pixels = buffer!
    CVPixelBufferLockBaseAddress(pixels, [])
    memset(CVPixelBufferGetBaseAddress(pixels), Int32(90 + frame / 10), CVPixelBufferGetDataSize(pixels))
    CVPixelBufferUnlockBaseAddress(pixels, [])
    guard adaptor.append(pixels, withPresentationTime: CMTime(value: Int64(frame), timescale: 30)) else {
        throw writer.error!
    }
}
input.markAsFinished()
let finished = DispatchSemaphore(value: 0)
writer.finishWriting { finished.signal() }
finished.wait()
guard writer.status == .completed else { throw writer.error! }
print("synthetic video ready")
